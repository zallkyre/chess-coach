#!/usr/bin/env python3
"""Coach Mode server.

Serves the app and proxies Groq chat. The API key is resolved on this side --
from the secrets-mcp vault (or GROQ_API_KEY) -- so it never reaches the browser,
the JS bundle, or localStorage.

    python proxy.py            -> http://127.0.0.1:8777
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8777"))
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-20b"

# secrets-mcp lives next door; import it without installing
sys.path.insert(0, str(ROOT.parent / "secrets-mcp"))


def groq_key():
    """Return (key, source). Never logs the key."""
    env = os.environ.get("GROQ_API_KEY")
    if env:
        return env, "env"
    try:
        from secrets_mcp import store  # type: ignore

        return store.get("groq"), "vault"
    except Exception as exc:  # no vault, no secret, no package
        print(f"[secrets] {type(exc).__name__}: {exc}", file=sys.stderr)
        return None, None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    def _json(self, code: int, obj: dict):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/api/health"):
            key, src = groq_key()
            return self._json(200, {"key": bool(key), "source": src, "model": MODEL})
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/chat"):
            return self._json(404, {"error": "not found"})

        # same-origin only
        origin = self.headers.get("Origin", "")
        if origin and not (origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")):
            return self._json(403, {"error": "cross-origin blocked"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._json(400, {"error": "messages required"})
        messages = messages[-12:]

        key, src = groq_key()
        if not key:
            return self._json(503, {
                "error": "no groq key in the vault",
                "hint": "python -m secrets_mcp  /  or set GROQ_API_KEY",
            })

        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": float(body.get("temperature", 0.85)),
            "max_completion_tokens": int(body.get("max_tokens", 400)),
            "reasoning_effort": "low",
        }
        req = urllib.request.Request(
            GROQ_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                # Cloudflare in front of Groq 403s the default Python-urllib UA
                "User-Agent": "coach-mode/1.0 (+local; python-urllib)",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            return self._json(502, {"error": f"groq {exc.code}", "detail": detail})
        except Exception as exc:
            return self._json(502, {"error": f"groq unreachable: {type(exc).__name__}"})

        # bump the use counter, never the value
        if src == "vault":
            try:
                from secrets_mcp import store  # type: ignore

                store.touch("groq")
            except Exception:
                pass

        try:
            content = data["choices"][0]["message"].get("content") or ""
        except Exception:
            content = ""
        return self._json(200, {"content": content.strip(), "source": src, "model": MODEL})


def main():
    key, src = groq_key()
    print()
    print("  COACH MODE")
    print(f"  http://127.0.0.1:{PORT}")
    print(f"  groq: {'ready via ' + str(src) if key else 'NO KEY (chat falls back to canned lines)'}")
    print("  ctrl+c to stop")
    print()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
