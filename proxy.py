#!/usr/bin/env python3
"""Coach Mode server.

Serves the app, proxies chat to any OpenAI-compatible provider, and keeps the
API keys out of the browser: they live in the secrets-mcp vault (DPAPI) and are
resolved here, per request.

    python proxy.py            -> http://127.0.0.1:8777
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8777"))
GROQ_UA = "coach-mode/1.0 (+localhost chess coach)"

# id -> base url, default model, vault handle (None = no key needed)
PROVIDERS = {
    "groq":       {"label": "Groq",        "base": "https://api.groq.com/openai/v1",    "model": "openai/gpt-oss-20b", "handle": "groq"},
    "openai":     {"label": "OpenAI",      "base": "https://api.openai.com/v1",         "model": "gpt-4o-mini",        "handle": "openai"},
    "openrouter": {"label": "OpenRouter",  "base": "https://openrouter.ai/api/v1",      "model": "openai/gpt-oss-20b", "handle": "openrouter"},
    "ollama":     {"label": "Ollama",      "base": "http://127.0.0.1:11434/v1",         "model": "llama3.1",           "handle": None},
}

# secrets-mcp lives next door in the dev tree; the installed build next to the exe
for candidate in (ROOT.parent / "secrets-mcp", ROOT):
    if (candidate / "secrets_mcp").is_dir():
        sys.path.insert(0, str(candidate))
        break

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def store():
    from secrets_mcp import store as s  # type: ignore

    return s


def has_secret(name: str) -> bool:
    try:
        return name in store().names()
    except Exception:
        return False


def get_secret(name: str):
    try:
        return store().get(name)
    except Exception:
        return None


def provider_status():
    out = []
    for pid, cfg in PROVIDERS.items():
        ready = True if cfg["handle"] is None else has_secret(cfg["handle"])
        out.append({
            "id": pid, "label": cfg["label"], "model": cfg["model"],
            "handle": cfg["handle"], "ready": ready, "keyless": cfg["handle"] is None,
        })
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    # ---------------- helpers ----------------
    def _json(self, code: int, obj: dict):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _local_only(self) -> bool:
        origin = self.headers.get("Origin", "")
        return not origin or origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")

    # ---------------- routes ----------------
    def do_GET(self):
        if self.path.startswith("/api/health"):
            provs = provider_status()
            ready = [p for p in provs if p["ready"]]
            return self._json(200, {
                "key": bool(ready), "provider": ready[0]["id"] if ready else None,
                "providers": provs, "count": len(ready),
            })
        return super().do_GET()

    def do_POST(self):
        if not self._local_only():
            return self._json(403, {"error": "cross-origin blocked"})
        if self.path.startswith("/api/import"):
            return self.import_key()
        if self.path.startswith("/api/chat"):
            return self.chat()
        return self._json(404, {"error": "not found"})

    def import_key(self):
        """Store a key in the DPAPI vault. The value passes through once and is
        never returned, logged, or written anywhere else."""
        try:
            b = self._body()
            handle = (b.get("handle") or "").strip().lower()
            value = (b.get("key") or "").strip()
        except Exception:
            return self._json(400, {"error": "bad json"})
        if not HANDLE_RE.match(handle):
            return self._json(400, {"error": "invalid handle (a-z, 0-9, -, _; max 32)"})
        if len(value) < 8:
            return self._json(400, {"error": "that does not look like a key"})
        try:
            res = store().put(handle, value)
        except Exception as exc:
            return self._json(500, {"error": f"vault refused it: {type(exc).__name__}"})
        return self._json(200, {"ok": True, "handle": res["name"], "hint": res["hint"]})

    def chat(self):
        try:
            b = self._body()
        except Exception:
            return self._json(400, {"error": "bad json"})

        messages = b.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._json(400, {"error": "messages required"})
        messages = messages[-12:]

        pid = (b.get("provider") or "groq").lower()
        cfg = PROVIDERS.get(pid)
        if not cfg:
            return self._json(400, {"error": f"unknown provider {pid!r}"})

        key = None
        if cfg["handle"]:
            key = get_secret(cfg["handle"])
            if not key:
                return self._json(503, {"error": f"no key stored for {cfg['label']}", "provider": pid})

        model = (b.get("model") or cfg["model"]).strip() or cfg["model"]
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(b.get("temperature", 0.85)),
            "max_tokens": int(b.get("max_tokens", 400)),
        }
        # gpt-oss on Groq thinks out loud and eats the budget; keep it terse
        if pid == "groq" and "gpt-oss" in model:
            payload["reasoning_effort"] = "low"
            payload["max_completion_tokens"] = payload.pop("max_tokens")

        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": GROQ_UA}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        req = urllib.request.Request(
            cfg["base"].rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            return self._json(502, {"error": f"{cfg['label']} {exc.code}", "detail": detail})
        except Exception as exc:
            return self._json(502, {"error": f"{cfg['label']} unreachable: {type(exc).__name__}"})

        if cfg["handle"]:
            try:
                store().touch(cfg["handle"])
            except Exception:
                pass

        try:
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if not content and msg.get("reasoning"):
                content = ""
        except Exception:
            content = ""
        return self._json(200, {"content": content, "provider": pid, "model": model})


def serve(open_browser: bool = False):
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/index.html")).start()
    provs = provider_status()
    print()
    print("  COACH MODE")
    print(f"  http://127.0.0.1:{PORT}")
    for p in provs:
        print(f"    {p['label']:<11} {'ready' if p['ready'] else 'no key'}")
    print("  ctrl+c to stop")
    print()
    httpd.serve_forever()


if __name__ == "__main__":
    serve(open_browser="--open" in sys.argv)
