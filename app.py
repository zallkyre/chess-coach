#!/usr/bin/env python3
"""Chess Coach — native desktop app.

Tkinter UI, native Stockfish, python-chess rules. No browser, no webview.

    python app.py
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import chess
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageOps, ImageTk

APP = "Chess Coach"
NOISE = 15                     # cp we do not count against you
GRADE_DEPTH = 14
MOVE_TIME_MS = 150
REVIEW_DEPTH = 18
SLIDE_MS = 170
SIDEBAR_W = 46                 # width of the button strip left of the board


def depth_for(level: int) -> int:
    """Search depth for a given difficulty. Low levels barely look ahead."""
    if level <= 3:
        return 1
    if level <= 6:
        return 2
    if level <= 10:
        return 4
    if level <= 14:
        return 7
    if level <= 17:
        return 10
    return 13


def random_chance(level: int) -> float:
    """How often the opponent just plays a random legal move."""
    table = {1: 0.50, 2: 0.40, 3: 0.30, 4: 0.20, 5: 0.14, 6: 0.10,
             7: 0.07, 8: 0.05, 9: 0.04, 10: 0.03, 11: 0.02, 12: 0.01}
    return table.get(level, 0.0)


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
def res(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def data_dir() -> Path:
    d = Path(os.environ.get("APPDATA", Path.home())) / "ChessCoach"
    d.mkdir(parents=True, exist_ok=True)
    return d


def engine_path():
    """Stockfish is not shipped in the app bundle - the installer fetches it
    from the official releases. Look where it could reasonably live."""
    cands = []
    env = os.environ.get("CHESSCOACH_ENGINE")
    if env:
        cands.append(Path(env))
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).parent / "engine" / "stockfish.exe")
        cands.append(Path(sys.executable).parent / "stockfish.exe")
    here = Path(__file__).resolve().parent
    cands += [here / "engine" / "stockfish.exe", here / "stockfish.exe"]
    cands.append(data_dir() / "engine" / "stockfish.exe")
    cands.append(res("engine", "stockfish.exe"))
    for c in cands:
        try:
            if c.is_file() and c.stat().st_size > 1_000_000:
                return c
        except OSError:
            pass
    from shutil import which

    found = which("stockfish")
    return Path(found) if found else None


# secrets-mcp lives next door in the dev tree, bundled inside the exe
for candidate in (Path(__file__).resolve().parent.parent / "secrets-mcp", res()):
    if (candidate / "secrets_mcp").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------
class Engine:
    def __init__(self, path: Path):
        si = None
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self.p = subprocess.Popen(
            [str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, startupinfo=si,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        self.q: queue.Queue[str] = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        self.send("uci")
        self.wait(lambda l: l == "uciok", 10)
        self.send("setoption name Threads value 2")
        self.send("setoption name Hash value 64")
        self.send("setoption name Ponder value false")
        self.send("isready")
        self.wait(lambda l: l == "readyok", 10)
        self.skill(20)

    def _read(self):
        try:
            for line in self.p.stdout:
                self.q.put(line.strip())
        except Exception:
            pass

    def send(self, cmd: str):
        try:
            self.p.stdin.write(cmd + "\n")
            self.p.stdin.flush()
        except Exception:
            pass

    def wait(self, pred, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                return None
            if pred(line):
                return line
        return None

    def skill(self, level: int):
        self.send(f"setoption name Skill Level value {max(0, min(20, int(level)))}")

    def _search(self, fen: str, depth: int, timeout=12.0, moves=None, multipv=1):
        """One bounded search. Returns lines, best first:
        [{"cp": white-view int, "mate": int | None, "uci": str}]"""
        with self.lock:
            found = {}
            self.skill(20)                     # fuzzed scores would poison grading
            self.send(f"setoption name MultiPV value {max(1, int(multipv))}")
            self.send("position fen " + fen)
            go = f"go depth {depth}" + (f" searchmoves {moves}" if moves else "")
            self.send(go)
            end = time.time() + timeout
            while time.time() < end:
                try:
                    line = self.q.get(timeout=max(0.01, end - time.time()))
                except queue.Empty:
                    break
                if line.startswith("info"):
                    m = re.search(r"score (cp|mate) (-?\d+)", line)
                    if not m:
                        continue
                    idx_m = re.search(r"multipv (\d+)", line)
                    idx = int(idx_m.group(1)) if idx_m else 1
                    e = found.setdefault(idx, {"cp": 0, "mate": None, "uci": None})
                    if m.group(1) == "cp":
                        e["cp"], e["mate"] = int(m.group(2)), None
                    else:
                        e["mate"], e["cp"] = int(m.group(2)), None
                    pv_m = re.search(r" pv (\S+)", line)
                    if pv_m:
                        e["uci"] = pv_m.group(1)
                elif line.startswith("bestmove"):
                    break
            if multipv > 1:
                self.send("setoption name MultiPV value 1")
            side = "w" if fen.split()[1] == "w" else "b"
            out = []
            for idx in sorted(found):
                e = found[idx]
                mate = e["mate"]
                if mate is not None:
                    val = 10000 - abs(mate) * 10 if mate > 0 else -10000 + abs(mate) * 10
                else:
                    val = e["cp"] or 0
                out.append({"cp": val if side == "w" else -val,
                            "mate": mate, "uci": e["uci"]})
            return out

    def analyze(self, fen: str, depth: int, timeout=12.0):
        """Returns (cp_white, mate_white, best_uci). Always bounded."""
        lines = self._search(fen, depth, timeout)
        if not lines:
            return 0, None, None
        top = lines[0]
        return top["cp"], top["mate"], top["uci"]

    def grade(self, fen: str, uci: str, depth: int, timeout=10.0):
        """Score the played move against the engine's best from the same root
        and depth. Same-root comparison keeps tempo out of the loss; comparing
        the position before the move with the one after it cannot."""
        lines = self._search(fen, depth, timeout, multipv=2)
        best = lines[0] if lines else {"cp": 0, "mate": None, "uci": None}
        second = lines[1] if len(lines) > 1 else None
        if best.get("uci") == uci:
            played = best                     # already the top move
        else:
            played_lines = self._search(fen, depth, timeout, moves=uci)
            played = played_lines[0] if played_lines else None
        return {"best": best, "second": second, "played": played}

    def play(self, fen: str, level: int, depth: int | None = None, ms: int = MOVE_TIME_MS):
        """Pick a move at a given strength. Never full strength."""
        with self.lock:
            best = None
            self.skill(level)
            self.send("position fen " + fen)
            self.send(f"go depth {depth}" if depth else f"go movetime {ms}")
            end = time.time() + (6.0 if depth else ms / 1000 + 6)
            while time.time() < end:
                try:
                    line = self.q.get(timeout=max(0.01, end - time.time()))
                except queue.Empty:
                    break
                if line.startswith("bestmove"):
                    parts = line.split()
                    best = parts[1] if len(parts) > 1 else None
                    break
            self.skill(20)
            return None if not best or best == "(none)" else best

    def close(self):
        try:
            self.send("quit")
            self.p.terminate()
        except Exception:
            pass



# --------------------------------------------------------------------------
# pieces drawn with PIL (same flat style as the web build)
# --------------------------------------------------------------------------
PIECE_FILL = {"w": "#f7f9fc", "b": "#2a3040"}
PIECE_STROKE = {"w": "#20262f", "b": "#0b0e13"}
BASE = [(20, 80), (80, 80), (82, 91), (18, 91)]


def _poly(d, pts, fill, stroke, w, s=1):
    p = [(x * s, y * s) for x, y in pts]
    d.polygon(p, fill=fill)
    d.line(p + [p[0]], fill=stroke, width=w, joint="curve")


def _ellipse(d, box, fill, stroke, w, s=1):
    d.ellipse([box[0] * s, box[1] * s, box[2] * s, box[3] * s], fill=fill, outline=stroke, width=w)


def draw_piece(kind, color, size):
    s = 4
    img = Image.new("RGBA", (100 * s, 100 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill, stroke = PIECE_FILL[color], PIECE_STROKE[color]
    w = 4 * s

    if kind == "p":
        _ellipse(d, (37, 15, 63, 41), fill, stroke, w, s)
        _poly(d, [(38, 40), (62, 40), (68, 78), (32, 78)], fill, stroke, w, s)
    elif kind == "r":
        _poly(d, [(26, 17), (38, 17), (38, 26), (45, 26), (45, 17), (55, 17), (55, 26),
                  (62, 26), (62, 17), (74, 17), (74, 34), (26, 34)], fill, stroke, w, s)
        _poly(d, [(32, 34), (68, 34), (74, 78), (26, 78)], fill, stroke, w, s)
    elif kind == "n":
        _poly(d, [(34, 78), (34, 54), (30, 45), (41, 35), (36, 25), (45, 17), (56, 22),
                  (67, 35), (70, 54), (70, 78)], fill, stroke, w, s)
        _ellipse(d, (49, 30, 56, 37), stroke, stroke, 1, s)
    elif kind == "b":
        _ellipse(d, (37, 16, 63, 44), fill, stroke, w, s)
        _ellipse(d, (46, 8, 54, 16), stroke, stroke, w, s)
        _poly(d, [(35, 43), (65, 43), (62, 51), (38, 51)], fill, stroke, w, s)
        _poly(d, [(33, 51), (67, 51), (72, 78), (28, 78)], fill, stroke, w, s)
    elif kind == "q":
        _poly(d, [(24, 42), (29, 21), (42, 33), (50, 15), (58, 33), (71, 21), (76, 42)],
              fill, stroke, w, s)
        _poly(d, [(29, 42), (71, 42), (75, 78), (25, 78)], fill, stroke, w, s)
    else:  # king
        _poly(d, [(46, 9), (54, 9), (54, 17), (62, 17), (62, 25), (54, 25), (54, 33),
                  (46, 33), (46, 25), (38, 25), (38, 17), (46, 17)], fill, stroke, w, s)
        _poly(d, [(29, 36), (71, 36), (75, 78), (25, 78)], fill, stroke, w, s)

    _poly(d, BASE, fill, stroke, w, s)
    return img.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------
# coach brain (vault-backed, several providers)
# --------------------------------------------------------------------------
PROVIDERS = {
    "groq":       {"label": "Groq",       "base": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-20b", "handle": "groq"},
    "openai":     {"label": "OpenAI",     "base": "https://api.openai.com/v1",      "model": "gpt-4o-mini",        "handle": "openai"},
    "openrouter": {"label": "OpenRouter", "base": "https://openrouter.ai/api/v1",   "model": "openai/gpt-oss-20b", "handle": "openrouter"},
    "ollama":     {"label": "Ollama",     "base": "http://127.0.0.1:11434/v1",      "model": "llama3.1",           "handle": None},
}

RAY_SYSTEM = (
    "You are Ray, a chess coach inside a small desktop app. You are a club player, "
    "roughly 1400, not a master and not an engine. Voice: casual, dry, a little "
    "self-deprecating. 1-2 short sentences, max 45 words. No lists, no emoji, no "
    "markdown. You get the real position below. Never invent engine lines."
)


def vault():
    try:
        from secrets_mcp import store  # type: ignore

        return store
    except Exception:
        return None


def get_key(handle: str):
    s = vault()
    if not s or not handle:
        return None
    try:
        return s.get(handle)
    except Exception:
        return None


def has_key(handle: str) -> bool:
    s = vault()
    if not s or not handle:
        return False
    try:
        return handle in s.names()
    except Exception:
        return False


def put_key(handle: str, value: str):
    s = vault()
    if not s:
        raise RuntimeError("secrets-mcp not available")
    return s.put(handle, value)


def ask_ray(provider: str, model: str, messages, timeout=45) -> str:
    cfg = PROVIDERS[provider]
    key = None
    if cfg["handle"]:
        key = get_key(cfg["handle"])
        if not key:
            raise RuntimeError(f"no key stored for {cfg['label']}")

    payload = {"model": model or cfg["model"], "messages": messages,
               "temperature": 0.85, "max_tokens": 400}
    if provider == "groq" and "gpt-oss" in payload["model"]:
        payload["reasoning_effort"] = "low"
        payload["max_completion_tokens"] = payload.pop("max_tokens")

    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "coach-mode/2.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(cfg["base"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    msg = data["choices"][0]["message"]
    out = (msg.get("content") or "").strip()
    if not out:
        raise RuntimeError("empty reply")
    return out


# --------------------------------------------------------------------------
# static coach lines
# --------------------------------------------------------------------------
LINES = {
    "brilliant": ["you gave material away and it works. i'm not built for this.",
                  "brilliant. i would not have seen that on a good day.",
                  "a real sacrifice. i need a moment."],
    "great": ["that's the only move that holds. impressive.",
              "great move — everything else loses something.",
              "the one move. you found it. rude, frankly."],
    "best": ["that's the move. i didn't calculate it, i just agree with the engine.",
             "best on the board. ok, show-off.",
             "exactly what i'd have played. after checking. twice.",
             "clean. genuinely."],
    "excellent": ["really nice. not the engine's first pick but the same idea.",
                  "strong. i'd shake your hand but i'm a drawing.",
                  "that's a move i'd find ten minutes later in the shower."],
    "good": ["fine move. not scary, not bad.", "solid. nothing to yell about.",
             "yeah, that works. you're not losing anything."],
    "book": ["theory. i've read a book. one book.",
             "that's mainline. boring in a trustworthy way.",
             "opening knowledge. the engine agrees and so does the book."],
    "miss": ["you had a win there and let it go. sorry.",
             "that's a miss — the tactic was right in front of you.",
             "half of me wants to tell you what it was. the other half is laughing."],
    "inaccuracy": ["hmm. not terrible, not great. there was something better.",
                   "slight wobble. i'm no grandmaster but that's loose.",
                   "you gave away a little something. not a disaster."],
    "mistake": ["that one hurts a bit. there was a cleaner path.",
                "that's a mistake. i've made four hundred tonight, so, solidarity.",
                "you dropped real value. not fatal. probably."],
    "blunder": ["ooh. that's a blunder. i'm sorry, i have to say it.",
                "yeah, that's bad. like, 'not telling my friends' bad.",
                "big one. don't panic — revert is right there."],
    "forced": ["only move available. nothing to rate."],
}
LABEL = {"brilliant": "Brilliant", "great": "Great", "best": "Best",
         "excellent": "Excellent", "good": "Good", "book": "Book",
         "inaccuracy": "Inaccuracy", "mistake": "Mistake", "miss": "Miss",
         "blunder": "Blunder", "forced": "Forced"}
COL = {"brilliant": "#26c2a3", "great": "#5b9bd5", "best": "#7fb14a",
       "excellent": "#96bc4b", "good": "#8fae84", "book": "#a4835f",
       "inaccuracy": "#f2c231", "mistake": "#e08a2e", "miss": "#e0584a",
       "blunder": "#cf4b42", "forced": "#8a93a6"}
GLYPH = {"brilliant": "!!", "great": "!", "best": "★", "excellent": "✓",
         "good": "·", "book": "≡", "inaccuracy": "?!", "mistake": "?",
         "miss": "✗", "blunder": "??", "forced": "="}

THEMES = {
    "green": ("#e9edd6", "#6f9250"),
    "blue": ("#dee3ed", "#4a6fa5"),
    "walnut": ("#e8d3b5", "#9b6a43"),
    "slate": ("#c9cfd8", "#4a5262"),
}

BG = "#101319"
CARD = "#1a1e27"
CARD2 = "#232833"
LINE = "#2d3341"
TEXT = "#e8ebf1"
MUTED = "#98a1b3"


def classify(raw_loss, is_best=False):
    loss = max(0, raw_loss - NOISE)
    if is_best:
        return "best"
    if loss <= 20:
        return "excellent"
    if loss <= 60:
        return "good"
    if loss <= 120:
        return "inaccuracy"
    if loss <= 250:
        return "mistake"
    return "blunder"


PIECE_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
               chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def is_sacrifice(board: chess.Board, move: chess.Move) -> bool:
    """True when the move really offers material: the piece lands on a square
    the opponent can take, and taking it wins material."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type == chess.KING:
        return False
    gain = 0
    if board.is_capture(move):
        victim = board.piece_at(move.to_square)
        gain = PIECE_VALUE.get(victim.piece_type, 1) if victim else 1
    if move.promotion:
        gain += PIECE_VALUE.get(move.promotion, 0) - 1
    if PIECE_VALUE.get(piece.piece_type, 0) - gain < 2:
        return False
    after = board.copy()
    after.push(move)
    sq, them, mine = move.to_square, not piece.color, piece.color
    attackers = after.attackers(them, sq)
    if not attackers:
        return False
    defenders = after.attackers(mine, sq)
    if not defenders:
        return True
    cheapest_att = min(PIECE_VALUE.get(after.piece_at(s).piece_type, 0) for s in attackers)
    cheapest_def = min(PIECE_VALUE.get(after.piece_at(s).piece_type, 0) for s in defenders)
    return cheapest_att < cheapest_def


# common mainlines, used only for the "Book" badge
BOOK_LINES = """
e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O
e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6
e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Na5
e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Nf6 Nc3 Bb4
e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5 Bb4
e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4 d5
e4 e5 Nc3 Nf6 f4 d5 fxe5 Nxe4
e4 e5 f4 exf4 Nf3 g5
e4 e5 Bc4 Nf6 d3 c6 Nf3 d5
e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6
e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 g6
e4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7
e4 c5 c3 d5 exd5 Qxd5 d4 Nf6
e4 e6 d4 d5 Nc3 Nf6 Bg5 Be7
e4 e6 d4 d5 Nd2 Nf6 e5 Nfd7
e4 e6 d4 d5 exd5 exd5 Nf3 Nf6
e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5
e4 c6 d4 d5 e5 c5
e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6
e4 d5 exd5 Nf6 d4 Nxd5 Nf3 g6
e4 Nf6 e5 Nd5 d4 d6 Nf3 dxe5 Nxe5 c6
e4 g6 d4 Bg7 Nc3 d6 f4 Nf6
e4 d6 d4 Nf6 Nc3 g6 f4 Bg7
d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O
d4 d5 c4 c6 Nf3 Nf6 Nc3 e6 Bg5 dxc4
d4 d5 c4 dxc4 Nf3 Nf6 e3 e6 Bxc4 c5
d4 d5 c4 e5 dxe5 d4 Nf3 Nc6
d4 d5 Nf3 Nf6 c4 e6
d4 d5 Bf4 Nf6 e3 e6 Nf3 c5
d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O
d4 Nf6 c4 e6 Nc3 Bb4 e3 O-O
d4 Nf6 c4 e6 Nf3 b6 g3 Bb7 Bg2 Be7
d4 Nf6 c4 c5 d5 b5
d4 f5 g3 Nf6 Bg2 e6 Nf3 Be7
c4 e5 Nc3 Nf6 Nf3 Nc6 g3 d5
c4 c5 Nf3 Nf6 d4 cxd4 Nxd4 e6
Nf3 d5 g3 Nf6 Bg2 e6 O-O Be7
Nf3 Nf6 c4 e6 g3 d5 Bg2 Be7
"""


def _book_index():
    out = set()
    for line in BOOK_LINES.strip().splitlines():
        toks = line.split()
        for i in range(1, len(toks) + 1):
            out.add(tuple(toks[:i]))
    return out


BOOK_MOVES = _book_index()


def grade_move(loss, is_best, gap, best_cp, played_cp, best_mate, sac, book, only):
    """The badge for one move: brilliant > great > best > excellent > good >
    book > inaccuracy > mistake > miss > blunder."""
    if only:
        return "forced"
    if book:
        return "book"
    had_win = (best_mate is not None and best_mate > 0) or best_cp >= 200
    if had_win and not is_best and played_cp < 50:
        return "miss"
    if is_best:
        if played_cp >= 9900:               # mate on the board, nothing to style
            return "best"
        if sac:
            return "brilliant"
        return "great" if gap >= 150 else "best"
    if sac and loss <= 30:
        return "brilliant"
    return classify(loss, False)


def fmt_eval(cp):
    if abs(cp) >= 9800:
        n = round((10000 - abs(cp)) / 10)
        return ("#" if cp > 0 else "-#") + str(max(0, n))
    return f"{cp/100:+.1f}"


# --------------------------------------------------------------------------
# app
# --------------------------------------------------------------------------
class CoachApp:
    SQ = 64

    def __init__(self, root: tk.Tk):
        self.root = root
        self.board = chess.Board()
        self.start_color = chess.WHITE
        self.player_color = chess.WHITE
        self.orientation = chess.WHITE
        self.stack = []              # list of (move, fen_before, cls, loss, only_move)
        self.selected = None
        self.last_move = None
        self.pre_eval = 0
        self.last_eval = 0
        self.game_over = False
        self.busy = False
        self.reviewing = False
        self.hint_cache = None
        self.hint_armed = False
        self.threat_arrows = []
        self.chat_log = []
        self.photo = {}
        self._drag = None
        self.ui_queue: queue.Queue = queue.Queue()

        self.settings = self.load_settings()
        exe = engine_path()
        if exe is None:
            raise RuntimeError(
                "Stockfish was not found.\n\n"
                "Re-run the installer, or drop stockfish.exe in:\n"
                + str(data_dir() / "engine") + "\n\n"
                "Official builds: https://github.com/official-stockfish/Stockfish/releases")
        self.engine = Engine(exe)
        self.build_ui()
        self.new_game(first=True)

    # ---------------- settings ----------------
    def load_settings(self):
        d = {"difficulty": 5, "theme": "green", "threats": True, "dots": True,
             "coords": True, "evalbar": True, "talk": True, "review": True,
             "locked": False, "provider": "groq", "model": ""}
        try:
            d.update(json.loads((data_dir() / "settings.json").read_text()))
        except Exception:
            pass
        return d

    def save_settings(self):
        try:
            (data_dir() / "settings.json").write_text(json.dumps(self.settings, indent=2))
        except Exception:
            pass

    # ---------------- ui ----------------
    def ui(self, fn):
        """Hand work to the Tk thread. Tk is not thread-safe, so worker
        threads never touch widgets or call after() themselves."""
        self.ui_queue.put(fn)

    def _pump(self):
        try:
            for _ in range(12):
                try:
                    fn = self.ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    pass
        finally:
            self.root.after(25, self._pump)

    def build_ui(self):
        r = self.root
        r.title(APP)
        r.configure(bg=BG)
        r.geometry("1120x900")
        r.minsize(980, 720)
        try:
            r.iconphoto(True, ImageTk.PhotoImage(
                Image.open(res("assets", "icon-256.png"))))
        except Exception:
            pass

        outer = tk.Frame(r, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=12)

        left = tk.Frame(outer, bg=BG)
        left.pack(side="left", fill="y")
        right = tk.Frame(outer, bg=BG, width=352)
        right.pack(side="right", fill="both", expand=True, padx=(16, 0))
        right.pack_propagate(False)

        self.build_header(left)

        boardarea = tk.Frame(left, bg=BG)
        boardarea.pack()
        self.build_sidebar(boardarea)
        boardrow = tk.Frame(boardarea, bg=BG)
        boardrow.pack(side="left")
        self.evalbar = tk.Canvas(boardrow, width=16, height=self.SQ * 8, bg="#0a0c11",
                                 highlightthickness=1, highlightbackground=LINE)
        self.evalbar.pack(side="left", padx=(0, 6))
        self.canvas = tk.Canvas(boardrow, width=self.SQ * 8, height=self.SQ * 8,
                                highlightthickness=0, bg=THEMES[self.settings["theme"]][0])
        self.canvas.pack(side="left")
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        self.capstrip = tk.Label(left, text="", bg=BG, fg="#8d96a8",
                                 font=("Consolas", 10), height=2)
        self.capstrip.pack(fill="x", pady=(6, 0))

        self.build_panel(right)
        self.bind_keys()
        self.root.attributes("-topmost", bool(self.settings.get("locked")))
        self._sync_side_buttons()
        self.root.after(25, self._pump)
        self.render()

    def build_sidebar(self, parent):
        """Slim vertical strip of window/board controls next to the board."""
        bar = tk.Frame(parent, bg=BG, width=SIDEBAR_W)
        bar.pack(side="left", fill="y", padx=(0, 8))
        bar.pack_propagate(False)
        self.side_btns = {}
        for key, text, cmd in (("lock", "LOCK", self.toggle_lock),
                               ("fen", "FEN", self.copy_fen),
                               ("theme", "GREEN", self.cycle_theme),
                               ("full", "FULL", self.toggle_full)):
            b = tk.Button(bar, text=text, command=cmd, bg=CARD2, fg=TEXT,
                          relief="flat", font=("Consolas", 7, "bold"), bd=0,
                          cursor="hand2", pady=7,
                          activebackground="#2d3341", activeforeground=TEXT)
            b.pack(fill="x", pady=(0, 5))
            self.side_btns[key] = b

    def _sync_side_buttons(self):
        if not getattr(self, "side_btns", None):
            return
        locked = bool(self.settings.get("locked"))
        self.side_btns["lock"].configure(
            text="LOCK", bg="#7ec96b" if locked else CARD2,
            fg="#0d1117" if locked else TEXT)
        fen = self.side_btns["fen"]
        if fen.cget("text") != "COPIED":
            fen.configure(text="FEN", bg=CARD2, fg=TEXT)
        self.side_btns["theme"].configure(
            text=str(self.settings.get("theme", "green")).upper()[:6],
            bg=CARD2, fg=TEXT)
        self.side_btns["full"].configure(text="FULL", bg=CARD2, fg=TEXT)

    def toggle_lock(self):
        """Pin the window above everything else until the button is pressed again."""
        locked = bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", not locked)
        self.settings["locked"] = not locked
        self.save_settings()
        self._sync_side_buttons()
        self.say("locked on top. i'll hover over your other windows."
                 if not locked else "unlocked. i'll sit quietly like a normal app.")

    def copy_fen(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.board.fen())
        self.side_btns["fen"].configure(text="COPIED", bg="#7ec96b", fg="#0d1117")
        self.say("position copied. paste it anywhere that wants a FEN.")
        self.root.after(900, self._sync_side_buttons)

    def cycle_theme(self):
        names = list(THEMES)
        cur = self.settings.get("theme")
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.settings["theme"] = nxt
        if hasattr(self, "theme_var"):
            self.theme_var.set(nxt)
        self.save_settings()
        self.canvas.configure(bg=THEMES[nxt][0])
        self.render()
        self._sync_side_buttons()
        self.say(f"board theme: {nxt}.")

    def toggle_full(self):
        self.root.state("normal" if self.root.state() == "zoomed" else "zoomed")
        self._sync_side_buttons()

    def build_header(self, parent):
        w, h = self.SQ * 8 + 22, 168
        cv = tk.Canvas(parent, width=w, height=h, highlightthickness=1,
                       highlightbackground=LINE, bg=CARD, bd=0)
        cv.pack(pady=(0, 10), anchor="w", padx=(SIDEBAR_W + 8, 0))
        try:
            img = Image.open(res("assets", "banner-full.jpg")).convert("RGB")
            img = ImageOps.fit(img, (w * 2, h * 2), method=Image.LANCZOS)
            shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(shade)
            for x in range(img.size[0]):
                a = int(215 * max(0.0, 1 - x / (img.size[0] * 0.55)))
                d.line([(x, 0), (x, img.size[1])], fill=(10, 13, 18, a))
            for y in range(img.size[1]):
                b = int(170 * max(0.0, 1 - (img.size[1] - y) / (img.size[1] * 0.55)))
                d.line([(0, y), (img.size[0], y)], fill=(10, 13, 18, b))
            img = Image.alpha_composite(img.convert("RGBA"), shade)
            self.hero_bg = ImageTk.PhotoImage(img.convert("RGB").resize((w, h), Image.LANCZOS))
            cv.create_image(0, 0, image=self.hero_bg, anchor="nw")
        except Exception:
            pass
        try:
            icon = Image.open(res("assets", "icon-256.png")).resize((46, 46), Image.LANCZOS)
            self.hero_icon = ImageTk.PhotoImage(icon)
            cv.create_image(16, h - 34, image=self.hero_icon, anchor="w")
        except Exception:
            pass
        cv.create_text(72, h - 44, text="COACH MODE", anchor="w",
                       fill="#ffffff", font=("Segoe UI", 14, "bold"))
        cv.create_text(72, h - 22, text="stockfish underneath, opinions on top",
                       anchor="w", fill="#b9c2d2", font=("Segoe UI", 8))
        self.turn_lbl = tk.Label(parent, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.turn_lbl.pack(anchor="w", pady=(0, 6), padx=(SIDEBAR_W + 8, 0))

    def card(self, parent, **kw):
        f = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        f.pack(fill="x", pady=(0, 8), **kw)
        return f

    def build_panel(self, parent):
        # coach
        c = self.card(parent)
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=10, pady=10)
        try:
            av = Image.open(res("assets", "icon-256.png")).resize((40, 40), Image.LANCZOS)
            self.avatar = ImageTk.PhotoImage(av)
            tk.Label(inner, image=self.avatar, bg=CARD).pack(side="left", padx=(0, 9))
        except Exception:
            pass
        col = tk.Frame(inner, bg=CARD)
        col.pack(side="left", fill="both", expand=True)
        row = tk.Frame(col, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="Ray", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.lvl_tag = tk.Label(row, text="CASUAL", bg="#7ec96b", fg="#0d1117",
                                font=("Consolas", 7, "bold"), padx=5, pady=1)
        self.lvl_tag.pack(side="left", padx=6)
        self.engine_lbl = tk.Label(row, text="engine ready", bg=CARD, fg="#68718a",
                                   font=("Consolas", 7))
        self.engine_lbl.pack(side="right")
        self.say_lbl = tk.Label(col, text="starting stockfish…", bg="#0e1117", fg="#dce2ec",
                                font=("Segoe UI", 9), wraplength=250, justify="left",
                                anchor="w", padx=9, pady=8, height=4)
        self.say_lbl.pack(fill="x", pady=(5, 0))

        # rating
        c2 = self.card(parent)
        r2 = tk.Frame(c2, bg=CARD)
        r2.pack(fill="x", padx=10, pady=9)
        self.chip = tk.Label(r2, text="ready", bg="#2a3040", fg="#aab3c4",
                             font=("Consolas", 8, "bold"), padx=7, pady=4, width=11)
        self.chip.pack(side="left")
        self.rate_lbl = tk.Label(r2, text="make a move", bg=CARD, fg=MUTED,
                                 font=("Segoe UI", 9), anchor="w", justify="left",
                                 wraplength=210)
        self.rate_lbl.pack(side="left", padx=9, fill="x", expand=True)

        # buttons
        c3 = self.card(parent)
        btns = tk.Frame(c3, bg=CARD)
        btns.pack(fill="x", padx=8, pady=8)
        for text, cmd, col_bg in (("Hint", self.hint, CARD2), ("Revert", self.undo, CARD2),
                                  ("Flip", self.flip, CARD2), ("New", self.new_game, "#7ec96b")):
            fg = "#0d1117" if col_bg == "#7ec96b" else TEXT
            b = tk.Button(btns, text=text, command=cmd, bg=col_bg, fg=fg, relief="flat",
                          font=("Segoe UI", 9, "bold"), activebackground=col_bg,
                          activeforeground=fg, bd=0, cursor="hand2", pady=6)
            b.pack(side="left", fill="x", expand=True, padx=2)

        self.btn_analyze = tk.Button(
            c3, text="Analyze game", command=self.analyze_game, bg="#20252f",
            fg="#6f7789", relief="flat", font=("Segoe UI", 9, "bold"),
            activebackground="#2a3040", activeforeground=TEXT, bd=0,
            cursor="arrow", pady=7, state="disabled")
        self.btn_analyze.pack(fill="x", padx=8, pady=(0, 8))

        # moves
        c4 = self.card(parent)
        mf = tk.Frame(c4, bg="#0e1117")
        mf.pack(fill="both", expand=True, padx=7, pady=7)
        self.moves = tk.Text(mf, height=8, bg="#0e1117", fg="#cfd6e3", bd=0,
                             font=("Consolas", 9), wrap="none", cursor="arrow",
                             state="disabled", highlightthickness=0)
        sb = tk.Scrollbar(mf, command=self.moves.yview, width=8)
        self.moves.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.moves.pack(side="left", fill="both", expand=True)
        self.moves.tag_configure("num", foreground="#666f83")
        self.moves.bind("<Button-1>", self.on_move_click)

        # settings
        c5 = self.card(parent)
        s = tk.Frame(c5, bg=CARD)
        s.pack(fill="x", padx=10, pady=9)
        head = tk.Frame(s, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="DIFFICULTY", bg=CARD, fg=MUTED,
                 font=("Consolas", 7, "bold")).pack(side="left")
        self.diff_lbl = tk.Label(head, text="", bg=CARD, fg=TEXT,
                                 font=("Consolas", 8, "bold"))
        self.diff_lbl.pack(side="right")
        self.diffbar = tk.Canvas(s, height=30, bg=CARD, highlightthickness=0,
                                 cursor="hand2", bd=0)
        self.diffbar.pack(fill="x", pady=(2, 0))
        self.diffbar.bind("<ButtonPress-1>", self.on_diff_drag)
        self.diffbar.bind("<B1-Motion>", self.on_diff_drag)
        self.diffbar.bind("<Configure>", lambda e: self.draw_diffbar())
        ends = tk.Frame(s, bg=CARD)
        ends.pack(fill="x")
        tk.Label(ends, text="easy", bg=CARD, fg="#5b6478",
                 font=("Consolas", 7)).pack(side="left")
        tk.Label(ends, text="brutal", bg=CARD, fg="#5b6478",
                 font=("Consolas", 7)).pack(side="right")

        row2 = tk.Frame(s, bg=CARD)
        row2.pack(fill="x")
        tk.Label(row2, text="Board", bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(side="left")
        self.theme_var = tk.StringVar(value=self.settings["theme"])
        om = ttk.Combobox(row2, values=list(THEMES), textvariable=self.theme_var,
                          width=8, state="readonly")
        om.pack(side="right")
        om.bind("<<ComboboxSelected>>", lambda e: self.on_theme())

        opts = tk.Frame(s, bg=CARD)
        opts.pack(fill="x", pady=(7, 0))
        self.chk = {}
        for key, label in (("threats", "Threats"), ("dots", "Dots"),
                           ("coords", "Coords"), ("evalbar", "Eval bar")):
            v = tk.BooleanVar(value=self.settings[key])
            self.chk[key] = v
            tk.Checkbutton(opts, text=label, variable=v, bg=CARD, fg="#c3cad8",
                           selectcolor="#0e1117", activebackground=CARD,
                           activeforeground=TEXT, font=("Segoe UI", 8), bd=0,
                           command=lambda k=key: self.on_toggle(k)).pack(side="left")

        # chat
        c6 = self.card(parent)
        cf = tk.Frame(c6, bg=CARD)
        cf.pack(fill="x", padx=8, pady=8)
        self.chat_in = tk.Entry(cf, bg="#0e1117", fg=TEXT, insertbackground=TEXT,
                                relief="flat", font=("Segoe UI", 9))
        self.chat_in.pack(side="left", fill="x", expand=True, ipady=5, padx=(2, 6))
        self.chat_in.bind("<Return>", lambda e: self.send_chat())
        tk.Button(cf, text="Send", command=self.send_chat, bg=CARD2, fg=TEXT,
                  relief="flat", font=("Segoe UI", 8, "bold"), bd=0,
                  cursor="hand2", padx=10).pack(side="right")
        tk.Button(cf, text="key…", command=self.key_dialog, bg=CARD, fg=MUTED,
                  relief="flat", font=("Segoe UI", 8), bd=0, cursor="hand2",
                  padx=6).pack(side="right", padx=(0, 4))

        self.status_lbl = tk.Label(parent, text="", bg=BG, fg="#5b6478",
                                   font=("Consolas", 7))
        self.status_lbl.pack(anchor="w", pady=(2, 0))
        self.update_provider_status()

    def bind_keys(self):
        self.root.bind("<KeyPress>", self.on_key)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_key(self, e):
        if isinstance(e.widget, (tk.Entry, tk.Text)):
            return
        k = e.keysym.lower()
        if k == "h":
            self.hint()
        elif k == "u":
            self.undo()
        elif k == "n":
            self.new_game()
        elif k == "f":
            self.flip()
        elif k == "t":
            self.chk["threats"].set(not self.chk["threats"].get())
            self.on_toggle("threats")

    # ---------------- images ----------------
    def piece_photo(self, kind, color, size):
        key = (kind, color, size)
        if key not in self.photo:
            img = draw_piece(kind, color, size)
            self.photo[key] = ImageTk.PhotoImage(img)
        return self.photo[key]

    # ---------------- geometry ----------------
    def sq_xy(self, square: int):
        f = chess.square_file(square)
        r = chess.square_rank(square)
        if self.orientation == chess.WHITE:
            col, row = f, 7 - r
        else:
            col, row = 7 - f, r
        return col * self.SQ, row * self.SQ

    def xy_sq(self, x, y):
        col = int(x // self.SQ)
        row = int(y // self.SQ)
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        if self.orientation == chess.WHITE:
            f, r = col, 7 - row
        else:
            f, r = 7 - col, row
        return chess.square(f, r)

    def center(self, square):
        x, y = self.sq_xy(square)
        return x + self.SQ / 2, y + self.SQ / 2

    # ---------------- render ----------------
    def render(self):
        cv = self.canvas
        cv.delete("all")
        light, dark = THEMES[self.settings["theme"]]
        coords = self.settings["coords"]
        king_sq = self.board.king(self.board.turn) if self.board.is_check() else None

        for row in range(8):
            for col in range(8):
                if self.orientation == chess.WHITE:
                    rank, file = 7 - row, col
                else:
                    rank, file = row, 7 - col
                sq = chess.square(file, rank)
                x0, y0 = col * self.SQ, row * self.SQ
                fill = light if (file + rank) % 2 == 0 else dark
                cv.create_rectangle(x0, y0, x0 + self.SQ, y0 + self.SQ,
                                    fill=fill, width=0)
                if coords and row == 7:
                    cv.create_text(x0 + self.SQ - 4, y0 + self.SQ - 4,
                                   text=chess.FILE_NAMES[file], anchor="se",
                                   fill="#465a32" if fill == light else "#e8eeda",
                                   font=("Consolas", 7, "bold"))
                if coords and col == 0:
                    cv.create_text(x0 + 4, y0 + 3, text=str(rank + 1), anchor="nw",
                                   fill="#465a32" if fill == light else "#e8eeda",
                                   font=("Consolas", 7, "bold"))

        if self.last_move:
            for sq in (self.last_move.from_square, self.last_move.to_square):
                x, y = self.sq_xy(sq)
                cv.create_rectangle(x, y, x + self.SQ, y + self.SQ,
                                    fill="#dfdba6", width=0)
        if king_sq is not None:
            x, y = self.sq_xy(king_sq)
            cv.create_rectangle(x + 1, y + 1, x + self.SQ - 1, y + self.SQ - 1,
                                outline="#dd5a5a", width=3)
        if self.selected is not None:
            x, y = self.sq_xy(self.selected)
            cv.create_rectangle(x, y, x + self.SQ, y + self.SQ,
                                fill="#e9df82", width=0)
            if self.settings["dots"]:
                for m in self.board.legal_moves:
                    if m.from_square != self.selected:
                        continue
                    cx, cy = self.center(m.to_square)
                    if self.board.piece_at(m.to_square):
                        cv.create_oval(cx - 27, cy - 27, cx + 27, cy + 27,
                                       outline="#3c4a33", width=5)
                    else:
                        cv.create_oval(cx - 9, cy - 9, cx + 9, cy + 9,
                                       fill="#3c4a33", width=0)

        size = int(self.SQ * 0.92)
        for sq in chess.SQUARES:
            p = self.board.piece_at(sq)
            if not p:
                continue
            cx, cy = self.center(sq)
            kind = p.symbol().lower()
            color = "w" if p.color == chess.WHITE else "b"
            cv.create_image(cx, cy, image=self.piece_photo(kind, color, size),
                            tags=("pc", f"sq{sq}"))

        self.draw_threats()
        self.update_evalbar()
        self.update_captures()
        self.update_turn()

    def draw_threats(self):
        cv = self.canvas
        cv.delete("threat")
        if not self.settings["threats"] or self.game_over:
            return
        enemy = not self.player_color
        probe = self.board.copy(stack=False)
        probe.turn = enemy
        victims = []
        for sq in chess.SQUARES:
            p = probe.piece_at(sq)
            if not p or p.color != enemy:
                continue
            for m in probe.attacks(sq):
                tgt = self.board.piece_at(m)
                if tgt and tgt.color == self.player_color:
                    victims.append((sq, m))
        victims.sort(key=lambda v: self.board.piece_at(v[1]).piece_type, reverse=True)
        for a, b in victims[:8]:
            x1, y1 = self.center(a)
            x2, y2 = self.center(b)
            cv.create_line(x1, y1, x2, y2, fill="#e06464", width=5,
                           arrow="last", arrowshape=(13, 15, 6), tags="threat")

    def update_evalbar(self):
        self.evalbar.delete("all")
        h = self.SQ * 8
        if not self.settings["evalbar"]:
            return
        frac = max(-1.0, min(1.0, self.last_eval / 700))
        top = (1 - (frac + 1) / 2) * h
        self.evalbar.create_rectangle(0, top, 16, h, fill="#dfe4ec", width=0)
        self.evalbar.create_text(8, h - 10, text=fmt_eval(self.last_eval), anchor="s",
                                 fill="#8b93a5", font=("Consolas", 6, "bold"))

    def update_captures(self):
        start = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2,
                 chess.ROOK: 2, chess.QUEEN: 1}
        val = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
               chess.ROOK: 5, chess.QUEEN: 9}
        have = {chess.WHITE: {}, chess.BLACK: {}}
        for sq in chess.SQUARES:
            p = self.board.piece_at(sq)
            if p:
                have[p.color][p.piece_type] = have[p.color].get(p.piece_type, 0) + 1
        lost = {chess.WHITE: 0, chess.BLACK: 0}
        for color in (chess.WHITE, chess.BLACK):
            for pt, n in start.items():
                gone = max(0, n - have[color].get(pt, 0))
                lost[color] += gone * val[pt]
        mine = lost[not self.player_color]
        theirs = lost[self.player_color]
        diff = mine - theirs
        self.capstrip.configure(
            text=f"  you: {mine}   opponent: {theirs}   ({diff:+d})" if diff else
                 f"  you: {mine}   opponent: {theirs}")

    def update_turn(self):
        if self.game_over:
            t = "game over"
        elif self.reviewing:
            t = "reviewing…"
        elif self.board.turn == self.player_color:
            t = "your move"
        else:
            t = "ray is thinking…"
        extra = f"  ·  move {self.board.fullmove_number}  ·  eval {fmt_eval(self.last_eval)}"
        self.turn_lbl.configure(text=t + extra)

    # ---------------- input ----------------
    def on_press(self, e):
        if self.busy or self.game_over or self.reviewing:
            return
        if self.board.turn != self.player_color:
            return
        sq = self.xy_sq(e.x, e.y)
        if sq is None:
            return
        piece = self.board.piece_at(sq)

        if self.selected is not None and sq != self.selected:
            mv = self.find_move(self.selected, sq)
            if mv is not None:
                self.try_move(mv)
                return

        if piece and piece.color == self.player_color:
            self.selected = sq
            self.render()
            self._drag = {"from": sq, "x": e.x, "y": e.y, "moved": False,
                          "item": (self.canvas.find_withtag(f"sq{sq}") or (None,))[0]}
        else:
            self.selected = None
            self.render()

    def on_motion(self, e):
        if not self._drag:
            return
        if abs(e.x - self._drag["x"]) + abs(e.y - self._drag["y"]) > 4:
            self._drag["moved"] = True
        if self._drag["moved"] and self._drag["item"] is not None:
            self.canvas.coords(self._drag["item"], e.x, e.y)

    def on_release(self, e):
        drag = self._drag
        self._drag = None
        if not drag:
            return
        if not drag["moved"]:
            return
        sq = self.xy_sq(e.x, e.y)
        if sq is not None and sq != drag["from"]:
            mv = self.find_move(drag["from"], sq)
            if mv is not None:
                self.try_move(mv)
                return
        self.render()

    def find_move(self, a, b):
        for m in self.board.legal_moves:
            if m.from_square == a and m.to_square == b:
                if m.promotion:
                    return self.ask_promotion(m)
                return m
        return None

    def ask_promotion(self, base_move):
        win = tk.Toplevel(self.root)
        win.title("Promote")
        win.configure(bg=CARD)
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        choice = {"v": None}
        tk.Label(win, text="promote to", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).pack(padx=16, pady=(12, 6))
        rowb = tk.Frame(win, bg=CARD)
        rowb.pack(padx=12, pady=(0, 12))
        for pt, name in ((chess.QUEEN, "q"), (chess.ROOK, "r"),
                         (chess.BISHOP, "b"), (chess.KNIGHT, "n")):
            img = self.piece_photo(name, "w", 48)
            b = tk.Button(rowb, image=img, bg="#e9edd6", relief="flat", bd=0,
                          cursor="hand2", padx=4, pady=4,
                          command=lambda p=pt: (choice.update(v=p), win.destroy()))
            b.pack(side="left", padx=4)
        win.wait_window()
        if choice["v"] is None:
            return None
        return chess.Move(base_move.from_square, base_move.to_square, promotion=choice["v"])

    # ---------------- game flow ----------------
    def try_move(self, move):
        self.hint_cache = None
        self.hint_armed = False
        fen_before = self.board.fen()
        only = self.board.legal_moves.count() == 1
        san = self.board.san(move)
        self.board.push(move)
        self.last_move = move
        self.selected = None
        self.stack.append({"move": move, "san": san, "fen": fen_before,
                           "cls": None, "loss": 0, "only": only,
                           "color": not self.board.turn})
        self.render_moves()
        self.render()
        self.busy = True

        if self.board.is_game_over():
            self.animate(move, lambda: self.end_game())
            return

        def start_grading():
            self.busy = True
            self.engine_lbl.configure(text="thinking…", fg="#dfbe57")
            threading.Thread(target=self._grade_then_reply, args=(move, san, only),
                             daemon=True).start()

        self.animate(move, start_grading)

    def rate(self, fen, move, only, depth, sans):
        """Grade one move in the position it was played from.

        Returns (badge, loss, cp_white). The loss is the engine's own score
        for the best move minus its score for the move you played, both from
        the same root at the same depth, so tempo is not counted as a loss."""
        g = self.engine.grade(fen, move.uci(), depth)
        sign = 1 if self.player_color == chess.WHITE else -1
        best = g["best"]
        played = g["played"] or best
        best_cp = sign * best["cp"]
        played_cp = sign * played["cp"]
        second = g["second"]
        gap = max(0, best_cp - sign * second["cp"]) if second else 0
        is_best = best["uci"] == move.uci()
        loss = max(0, best_cp - played_cp)
        cls = grade_move(loss=loss, is_best=is_best, gap=gap, best_cp=best_cp,
                         played_cp=played_cp, best_mate=best["mate"],
                         sac=is_sacrifice(chess.Board(fen), move),
                         book=tuple(sans) in BOOK_MOVES, only=only)
        return cls, loss, played["cp"]

    def _grade_then_reply(self, move, san, only):
        rec = self.stack[-1]
        sans = [r["san"] for r in self.stack]        # includes this move
        try:
            cls, loss, cp_white = self.rate(rec["fen"], move, only,
                                            GRADE_DEPTH, sans)
        except Exception:
            cls, loss, cp_white = "good", 0, self.last_eval
        sign = 1 if self.player_color == chess.WHITE else -1
        self.pre_eval = sign * cp_white
        self.ui(lambda: self.after_grade(cp_white, cls, loss, move))

    def after_grade(self, cp_white, cls, loss, move):
        self.last_eval = cp_white
        self.stack[-1]["cls"] = cls
        self.stack[-1]["loss"] = loss
        self.set_rating(cls, loss)
        self.render_moves()
        self.render()
        if self.settings["talk"]:
            self.say(random.choice(LINES.get(cls, LINES["good"])))

        if self.board.is_game_over():
            self.busy = False
            self.engine_lbl.configure(text="engine ready", fg="#7ec96b")
            self.end_game()
            return
        threading.Thread(target=self._engine_reply, daemon=True).start()

    def _engine_reply(self):
        fen = self.board.fen()
        level = self.settings["difficulty"]
        move = None
        chance = random_chance(level)
        if chance and random.random() < chance:
            legal = list(self.board.legal_moves)
            if legal:
                move = random.choice(legal).uci()
        if move is None:
            move = self.engine.play(fen, level, depth_for(level))
        time.sleep(0.30)          # let the player's own move land before the answer
        self.ui(lambda: self.after_reply(move))

    def after_reply(self, uci):
        if not uci:
            self.busy = False
            self.engine_lbl.configure(text="engine ready", fg="#7ec96b")
            return
        move = chess.Move.from_uci(uci)
        if move not in self.board.legal_moves:
            move = next(iter(self.board.legal_moves))
        fen_before = self.board.fen()
        san = self.board.san(move)
        self.board.push(move)
        self.last_move = move
        self.stack.append({"move": move, "san": san, "fen": fen_before,
                           "cls": "opp", "loss": 0, "only": False,
                           "color": not self.board.turn})
        self.render_moves()
        self.render()

        def settle():
            self.busy = False
            self.engine_lbl.configure(text="engine ready", fg="#7ec96b")
            if self.board.is_game_over():
                self.end_game()

        self.animate(move, settle)

    # ---------------- animation ----------------
    def animate(self, move, done=None):
        """Slide the piece from its old square to the new one, then call done."""
        cv = self.canvas
        cv.delete("slide")
        dest_tag = f"sq{move.to_square}"
        cv.itemconfigure(dest_tag, state="hidden")

        p = self.board.piece_at(move.to_square)
        kind = p.symbol().lower() if p else "p"
        color = "w" if (p and p.color == chess.WHITE) else "b"
        img = self.piece_photo(kind, color, int(self.SQ * 0.92))

        x1, y1 = self.center(move.from_square)
        x2, y2 = self.center(move.to_square)
        item = cv.create_image(x1, y1, image=img, tags="slide")
        steps = 10

        def step(i=1):
            t = i / steps
            e = 1 - (1 - t) ** 3
            cv.coords(item, x1 + (x2 - x1) * e, y1 + (y2 - y1) * e)
            if i < steps:
                cv.after(max(8, SLIDE_MS // steps), lambda: step(i + 1))
            else:
                cv.delete("slide")
                cv.itemconfigure(dest_tag, state="normal")
                if done:
                    done()

        cv.after(8, step)

    def set_rating(self, cls, loss):
        self.chip.configure(text=LABEL.get(cls, "?"), bg=COL.get(cls, "#2a3040"),
                            fg="#0d1117")
        notes = {"brilliant": "sacrifice, and it works",
                 "great": "only move that held",
                 "best": "engine's top choice",
                 "excellent": "nearly the top pick",
                 "good": "fine, small cost",
                 "book": "opening theory",
                 "miss": "you had a win there",
                 "forced": "only legal move"}
        if cls in notes:
            txt = notes[cls]
        else:
            txt = f"cost you {min(loss, 1500)/100:.2f} pawns"
        self.rate_lbl.configure(text=txt)

    def end_game(self):
        self.game_over = True
        self.busy = False
        if self.board.is_checkmate():
            msg = ("checkmate. you got me." if self.board.turn != self.player_color
                   else "checkmate. that's you done — i've been there.")
        elif self.board.is_stalemate():
            msg = "stalemate. nobody wins, everyone's annoyed."
        else:
            msg = "game over. draw, i think."
        self.say(msg + " hit analyze when you want the full breakdown.")
        self.set_rating("forced", 0)
        self.chip.configure(text="ready to analyze", bg="#2a3040", fg="#aab3c4")
        self.set_analyze_state("Analyze game", enabled=bool(self.stack))
        self.render()

    def set_analyze_state(self, text="Analyze game", enabled=False):
        """Enable the analyze button only once the game is over."""
        try:
            self.btn_analyze.configure(
                text=text,
                state="normal" if enabled else "disabled",
                bg="#7ec96b" if enabled else "#20252f",
                fg="#0d1117" if enabled else "#6f7789",
                cursor="hand2" if enabled else "arrow")
        except Exception:
            pass

    def analyze_game(self):
        """Full post-game pass: every position searched at REVIEW_DEPTH."""
        if self.reviewing or not self.game_over or not self.stack:
            return
        self.reviewing = True
        self.set_analyze_state("Analyzing…", enabled=False)
        self.chip.configure(text="review…", bg="#2a3040", fg="#aab3c4")
        self.say("going through the whole game. give me a minute.")
        threading.Thread(target=self._review, daemon=True).start()

    def _review_progress(self, done, total):
        self.chip.configure(text=f"review {done}/{total}")
        try:
            self.btn_analyze.configure(text=f"Analyzing {done}/{total}")
        except Exception:
            pass

    def _review(self):
        self.reviewing = True
        total = len(self.stack)
        for i, rec in enumerate(self.stack):
            if rec["color"] != self.player_color:
                rec["cls"] = "opp"
                rec["loss"] = 0
                self.ui(lambda i=i: self._review_progress(i + 1, total))
                continue
            sans = [r["san"] for r in self.stack[:i + 1]]
            try:
                cls, loss, _ = self.rate(rec["fen"], rec["move"], rec["only"],
                                         REVIEW_DEPTH, sans)
                rec["cls"] = cls
                rec["loss"] = loss
            except Exception:
                rec["cls"] = rec["cls"] or "good"
            self.ui(lambda i=i: self._review_progress(i + 1, total))
        self.reviewing = False
        self.ui(self.after_review)

    def after_review(self):
        self.reviewing = False
        self.render_moves()
        self.set_analyze_state("Analyze again", enabled=True)
        acc = self.accuracy()
        best = sum(1 for s in self.stack if s["cls"] == "best")
        blunders = sum(1 for s in self.stack if s["cls"] == "blunder")
        self.chip.configure(text="done", bg=COL["best"], fg="#0d1117")
        self.rate_lbl.configure(text=f"accuracy {acc}% · {best} best · {blunders} blunders")
        self.say(f"done. accuracy {acc}%. click any move to go back to it."
                 if acc is not None else "done.")
        self.render()

    def accuracy(self):
        pts = {"brilliant": 100, "great": 97, "best": 100, "excellent": 90,
               "good": 74, "book": 92, "inaccuracy": 60, "mistake": 35,
               "miss": 25, "blunder": 10, "forced": 72}
        rated = []
        for s in self.stack:
            mine = s["color"] == self.player_color
            if mine and s["cls"] in pts:
                rated.append(pts[s["cls"]])
        return round(sum(rated) / len(rated)) if rated else None

    # ---------------- controls ----------------
    def new_game(self, first=False):
        if not first and self.stack and not self.game_over:
            if not messagebox.askyesno(APP, "Abandon this game?"):
                return
        self.board.reset()
        self.stack = []
        self.selected = None
        self.last_move = None
        self.game_over = False
        self.reviewing = False
        self.busy = False
        self.hint_cache = None
        self.player_color = chess.WHITE
        self.orientation = chess.WHITE
        self.render_moves()
        self.render()
        self.chip.configure(text="ready", bg="#2a3040", fg="#aab3c4")
        self.set_analyze_state()
        self.rate_lbl.configure(text="make a move — i'll rate it")
        self.say("your move. i'm not a pro, i'm just loud.")

        def boot():
            cp, _, _ = self.engine.analyze(self.board.fen(), 12)
            self.pre_eval = cp
            self.ui(lambda: self._set_eval(cp))

        threading.Thread(target=boot, daemon=True).start()

    def _set_eval(self, cp):
        self.last_eval = cp
        self.update_evalbar()
        self.update_turn()

    def undo(self):
        if self.busy or self.reviewing or not self.stack:
            return
        # walk back over the opponent's reply first, then your own move, so the
        # board always comes back on your turn instead of theirs
        while self.stack and self.stack[-1]["color"] != self.player_color:
            self.stack.pop()
            self.board.pop()
        if self.stack:
            self.stack.pop()
            self.board.pop()
        self.last_move = self.stack[-1]["move"] if self.stack else None
        self.game_over = False
        self.reviewing = False
        self.busy = False
        self.selected = None
        self.hint_cache = None
        self.hint_armed = False
        self.canvas.delete("hint")
        self.set_analyze_state()
        self.say("took it back. we don't talk about it.")
        self.render_moves()
        self.render()
        threading.Thread(target=self._rebase, daemon=True).start()

    def _rebase(self):
        cp, _, _ = self.engine.analyze(self.board.fen(), 12)
        self.pre_eval = cp if self.player_color == chess.WHITE else -cp
        self.ui(lambda: self._set_eval(cp))

    def hint(self):
        if self.busy or self.game_over or self.board.turn != self.player_color:
            return
        if self.hint_armed and self.hint_cache:
            move = chess.Move.from_uci(self.hint_cache)
            self._hint_arrow(move)
            self.say(f"fine — {self.board.san(move)}. i'm no grandmaster, but i'd play that.")
            self.hint_armed = False
            self.hint_cache = None
            return
        self.say("gimme a sec…")

        def work():
            cp, _, best = self.engine.analyze(self.board.fen(), 18)
            self.ui(lambda: self._show_hint(best))

        threading.Thread(target=work, daemon=True).start()

    def _hint_arrow(self, move):
        self.canvas.delete("hint")
        x1, y1 = self.center(move.from_square)
        x2, y2 = self.center(move.to_square)
        self.canvas.create_line(x1, y1, x2, y2, fill="#7ec96b", width=9,
                                arrow="last", arrowshape=(16, 20, 7), tags="hint")

    def _show_hint(self, best):
        if not best:
            self.say("i've got nothing. worrying for both of us.")
            return
        move = chess.Move.from_uci(best)
        self.hint_cache = best
        self.hint_armed = True
        # first press only marks the piece. the destination stays hidden until
        # the second press, otherwise "hint" is just the answer.
        self.canvas.delete("hint")
        x, y = self.sq_xy(move.from_square)
        self.canvas.create_rectangle(x + 3, y + 3, x + self.SQ - 3, y + self.SQ - 3,
                                     outline="#7ec96b", width=3, tags="hint")
        p = self.board.piece_at(move.from_square)
        name = chess.piece_name(p.piece_type) if p else "piece"
        self.say(f"look at your {name} on {chess.square_name(move.from_square)}. "
                 "press hint again to just say it.")

    def flip(self):
        self.orientation = not self.orientation
        self.render()

    # ---------------- panel updates ----------------
    def say(self, text):
        self.say_lbl.configure(text=text)

    def render_moves(self):
        t = self.moves
        t.configure(state="normal")
        t.delete("1.0", "end")
        if not self.stack:
            t.insert("end", "no moves yet\n", ("num",))
        n = 0
        for i, rec in enumerate(self.stack):
            if rec["color"] == chess.WHITE:
                n += 1
                t.insert("end", f"{n:>2}. ", ("num",))
            elif i == 0 or self.stack[i - 1]["color"] == chess.WHITE:
                t.insert("end", "    ")
            cls = rec["cls"]
            tag = f"m{i}"
            t.insert("end", rec["san"], (tag,))
            if cls and cls != "opp":
                t.insert("end", GLYPH.get(cls, ""), (tag, cls))
            t.insert("end", "  ")
            if rec["color"] == chess.BLACK:
                t.insert("end", "\n")
        for k, colr in COL.items():
            t.tag_configure(k, foreground=colr)
        t.tag_configure("opp", foreground="#9aa3b5")
        t.configure(state="disabled")
        t.see("end")

    def on_move_click(self, e):
        if self.busy or self.reviewing:
            return
        idx = self.moves.index(f"@{e.x},{e.y}")
        for tag in self.moves.tag_names(idx):
            if tag.startswith("m") and tag[1:].isdigit():
                self.rewind_to(int(tag[1:]))
                return

    def rewind_to(self, ply):
        if ply >= len(self.stack):
            return
        target = self.stack[ply]
        self.board = chess.Board(target["fen"])
        self.stack = self.stack[:ply]
        self.last_move = self.stack[-1]["move"] if self.stack else None
        self.game_over = False
        self.reviewing = False
        self.selected = None
        self.set_analyze_state()
        self.say("rewound. no judgement. okay, maybe a little.")
        self.render_moves()
        self.render()
        threading.Thread(target=self._rebase, daemon=True).start()

    # ---------------- chat ----------------
    def update_provider_status(self):
        prov = self.settings["provider"]
        cfg = PROVIDERS.get(prov, PROVIDERS["groq"])
        if not vault():
            self.status_lbl.configure(text="secrets-mcp not found — chat uses canned lines")
            return
        ok = cfg["handle"] is None or has_key(cfg["handle"])
        self.status_lbl.configure(
            text=f"brain: {cfg['label']} · {cfg['model']} · "
                 + ("ready" if ok else "no key (click 'key…')"))

    def send_chat(self):
        text = self.chat_in.get().strip()
        if not text:
            return
        self.chat_in.delete(0, "end")
        if re.match(r"^(hint|help|\?+)$", text, re.I):
            self.hint()
            return
        self.say("…")
        threading.Thread(target=self._chat_work, args=(text,), daemon=True).start()

    def _chat_work(self, text):
        prov = self.settings["provider"]
        model = self.settings.get("model") or PROVIDERS[prov]["model"]
        ctx = (f"FEN: {self.board.fen()}\n"
               f"Player is {'White' if self.player_color else 'Black'}. "
               f"Opponent strength {self.settings['difficulty']}/20. "
               f"Eval {fmt_eval(self.last_eval)} (white's view).\n"
               f"Last move: " + (f"{self.stack[-1]['san']} ({self.stack[-1]['cls']})"
                                 if self.stack else "none") + "\n"
               f"Accuracy so far: {self.accuracy()}%")
        msgs = [{"role": "system", "content": RAY_SYSTEM},
                {"role": "system", "content": "Current position:\n" + ctx}]
        msgs += self.chat_log[-8:]
        msgs.append({"role": "user", "content": text})
        try:
            out = ask_ray(prov, model, msgs)
            self.chat_log += [{"role": "user", "content": text},
                              {"role": "assistant", "content": out}]
            self.chat_log = self.chat_log[-24:]
            self.ui(lambda: self.say(out))
        except Exception as exc:
            self.ui(lambda: self.say(f"can't reach {PROVIDERS[prov]['label']}: {exc}"))

    def key_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Coach brain")
        win.configure(bg=CARD)
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        tk.Label(win, text="Provider", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 2))
        pv = tk.StringVar(value=self.settings["provider"])
        cb = ttk.Combobox(win, values=list(PROVIDERS), textvariable=pv,
                          state="readonly", width=26)
        cb.grid(row=1, column=0, columnspan=2, sticky="we", padx=14)

        tk.Label(win, text="Model", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", padx=14, pady=(10, 2))
        mv = tk.StringVar(value=self.settings.get("model") or "")
        tk.Entry(win, textvariable=mv, bg="#0e1117", fg=TEXT, insertbackground=TEXT,
                 relief="flat", width=30).grid(row=3, column=0, columnspan=2,
                                               sticky="we", padx=14, ipady=4)

        tk.Label(win, text="API key (stored in the vault, never shown again)",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=4, column=0, sticky="w", padx=14, pady=(10, 2))
        kv = tk.StringVar()
        tk.Entry(win, textvariable=kv, show="•", bg="#0e1117", fg=TEXT,
                 insertbackground=TEXT, relief="flat", width=30).grid(
            row=5, column=0, columnspan=2, sticky="we", padx=14, ipady=4)

        note = tk.Label(win, text="", bg=CARD, fg="#8d96a8", font=("Segoe UI", 8))
        note.grid(row=6, column=0, columnspan=2, sticky="w", padx=14, pady=(6, 0))

        def sync_model(*_):
            cfg = PROVIDERS[pv.get()]
            mv.set(cfg["model"])
            ok = cfg["handle"] is None or has_key(cfg["handle"])
            note.configure(text=cfg["label"] + " · " + ("key ok" if ok else "no key yet"),
                           fg="#7ec96b" if ok else "#dfbe57")

        cb.bind("<<ComboboxSelected>>", sync_model)
        sync_model()

        def save():
            prov = pv.get()
            cfg = PROVIDERS[prov]
            self.settings["provider"] = prov
            self.settings["model"] = mv.get().strip()
            self.save_settings()
            key = kv.get().strip()
            if key and cfg["handle"]:
                try:
                    res_put = put_key(cfg["handle"], key)
                    note.configure(text=f"stored as {{{{{res_put['name']}}}}} {res_put['hint']}",
                                   fg="#7ec96b")
                except Exception as exc:
                    note.configure(text=f"could not store: {exc}", fg="#dd5a5a")
                    return
            self.update_provider_status()
            win.after(500, win.destroy)

        tk.Button(win, text="Save", command=save, bg="#7ec96b", fg="#0d1117",
                  relief="flat", font=("Segoe UI", 9, "bold"), bd=0,
                  cursor="hand2", padx=16, pady=6).grid(
            row=7, column=0, columnspan=2, pady=14)
        win.wait_window()
        self.update_provider_status()

    # ---------------- settings handlers ----------------
    def on_diff(self, value):
        v = int(float(value))
        self.settings["difficulty"] = v
        self.save_settings()
        names = {1: "beginner", 4: "beginner", 7: "casual", 10: "club",
                 14: "strong", 20: "ruthless"}
        name = "ruthless" if v > 15 else "strong" if v > 11 else "club" if v > 7 \
            else "casual" if v > 3 else "beginner"
        self.diff_lbl.configure(text=f"{name} · {v}")
        self.lvl_tag.configure(text=name.upper())
        if hasattr(self, "diffbar"):
            self.draw_diffbar()

    @staticmethod
    def _mix(a, b, t):
        a1, b1 = a.lstrip("#"), b.lstrip("#")
        ca = [int(a1[i:i+2], 16) for i in (0, 2, 4)]
        cb = [int(b1[i:i+2], 16) for i in (0, 2, 4)]
        return "#%02x%02x%02x" % tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))

    def _ramp(self, t):
        if t < 0.5:
            return self._mix("#4f9e6a", "#c9c35b", t / 0.5)
        return self._mix("#c9c35b", "#d9584f", (t - 0.5) / 0.5)

    def draw_diffbar(self):
        c = self.diffbar
        c.delete("all")
        w = c.winfo_width()
        if w < 40:
            return
        h, x0, x1 = 30, 10, w - 10
        y = h // 2
        t = max(0.0, min(1.0, (self.settings["difficulty"] - 1) / 19))
        # dim track
        c.create_line(x0, y, x1, y, fill="#2b3242", width=10, capstyle="round")
        # live gradient up to the handle
        steps = max(2, int((x1 - x0) * t / 3))
        for i in range(steps):
            p0 = x0 + (x1 - x0) * t * i / steps
            p1 = x0 + (x1 - x0) * t * (i + 1) / steps + 1
            c.create_line(p0, y, p1, y, fill=self._ramp(t * i / steps), width=10)
        hx = x0 + (x1 - x0) * t
        c.create_oval(hx - 9, y - 9, hx + 9, y + 9, fill="#ffffff", outline="#101319", width=2)

    def on_diff_drag(self, e):
        w = self.diffbar.winfo_width()
        if w < 40:
            return
        t = max(0.0, min(1.0, (e.x - 10) / (w - 20)))
        self.on_diff(str(round(1 + t * 19)))

    def on_theme(self):
        self.settings["theme"] = self.theme_var.get()
        self.save_settings()
        self.canvas.configure(bg=THEMES[self.settings["theme"]][0])
        self.render()

    def on_toggle(self, key):
        self.settings[key] = bool(self.chk[key].get())
        self.save_settings()
        self.render()

    def on_close(self):
        try:
            self.engine.close()
        finally:
            self.root.destroy()


def main():
    try:
        root = tk.Tk()
        app = CoachApp(root)
        app.on_diff(app.settings["difficulty"])
        root.mainloop()
    except Exception as exc:            # windowless exe: never die silently
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0, f"{type(exc).__name__}: {exc}", APP, 0x10)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
