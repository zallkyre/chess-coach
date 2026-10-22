#!/usr/bin/env python3
"""Chess Coach desktop launcher.

Starts the local server (proxy.py) and opens a chromeless Edge/Chrome window.
Closing that window shuts the server down. Double-clicking again just reopens
the window against the running server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Chess Coach"
PREFERRED_PORTS = (8777, 8788, 8799)


def app_dir() -> Path:
    """Where index.html / assets / vendor live (PyInstaller unpacks to _MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def probe(port: int):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.7) as r:
            return json.loads(r.read())
    except Exception:
        return None


def find_browser() -> str | None:
    roots = [os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
             os.environ.get("PROGRAMFILES", r"C:\Program Files"),
             os.environ.get("LOCALAPPDATA", "")]
    names = ["Microsoft\\Edge\\Application\\msedge.exe",
             "Google\\Chrome\\Application\\chrome.exe",
             "BraveSoftware\\Brave-Browser\\Application\\brave.exe"]
    for root in roots:
        if not root:
            continue
        for name in names:
            p = Path(root) / name
            if p.exists():
                return str(p)
    return None


def open_window(url: str):
    exe = find_browser()
    if exe:
        flags = [exe, f"--app={url}", "--window-size=1180,940", "--no-first-run",
                 "--no-default-browser-check"]
        try:
            proc = subprocess.Popen(flags)
            proc.wait()          # returns when the app window closes
            return True
        except Exception:
            pass
    webbrowser.open(url)         # last resort: normal tab
    return False


def main():
    import proxy  # bundled next to this file

    proxy.ROOT = app_dir()

    # already running? just show it again
    for port in PREFERRED_PORTS:
        if probe(port):
            open_window(f"http://127.0.0.1:{port}/index.html")
            return

    from http.server import ThreadingHTTPServer

    httpd = None
    port = 0
    for candidate in PREFERRED_PORTS:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), proxy.Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
        port = httpd.server_address[1]

    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/index.html"
    # give the server a beat to accept connections
    for _ in range(40):
        if probe(port):
            break
        time.sleep(0.05)

    if open_window(url):
        httpd.shutdown()          # window closed -> stop serving
    else:
        while True:               # normal tab: stay alive until the process is killed
            time.sleep(3600)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:      # never die silently in a windowless exe
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"{type(exc).__name__}: {exc}",
                                             APP_NAME, 0x10)
        except Exception:
            pass
        raise
