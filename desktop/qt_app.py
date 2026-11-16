"""Chess Coach — native desktop build (Qt / PySide6).

No Tkinter, no webview, no Electron: a real native window that paints the board
with QPainter and talks to the installed Stockfish over UCI.

    python desktop/qt_app.py

The chess logic, engine client, opening book, transposition cache and Ray's
voice all come from app.py, so the desktop and phone builds stay in sync.
"""

from __future__ import annotations

import io
import os
import random
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import chess
from PySide6.QtCore import (
    QEasingCurve, QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QIcon,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QProgressBar, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

import app as core

# --------------------------------------------------------------------------
# palette — one low-saturation green ambient, warm charcoal glass
# --------------------------------------------------------------------------
BG0 = QColor("#1e211d")
BG1 = QColor("#2a332a")
GLASS = QColor(38, 42, 36, 168)
GLASS_HI = QColor(255, 255, 255, 20)
STROKE = QColor(255, 255, 255, 26)
TEXT = QColor("#e9e6e2")
MUTED = QColor("#a09c96")
LIGHT = QColor("#eeeed2")
DARK = QColor("#769656")
LAST = QColor(186, 202, 68, 105)
SEL = QColor(255, 255, 51, 105)
HINT = QColor("#81b64c")
ARROW = QColor("#7fb14a")

SYM = {"brilliant": "!!", "great": "!", "best": "★", "excellent": "👍",
       "good": "✓", "book": "📖", "inaccuracy": "?!", "mistake": "?",
       "miss": "✕", "blunder": "??", "forced": "=", "opp": "·"}

# piece art, 100x100, same silhouettes as the phone build
PIECES = {
    "p": '<circle cx="50" cy="29" r="13"/><path d="M39 42h22c5 6 5 12 0 17H39c-5-5-5-11 0-17z"/><path d="M37 59h26l5 14H32z"/><rect x="27" y="73" width="46" height="11" rx="5.5"/>',
    "r": '<path d="M25 24h11v8h9v-8h10v8h9v-8h11v19H25z"/><path d="M33 43h34l-4 26H37z"/><path d="M29 69h42l4 12H25z"/><rect x="21" y="81" width="58" height="11" rx="5.5"/>',
    "n": '<path d="M31 84c0-15 6-26 18-33l-8-7 4-10 10 5c13 6 21 18 21 31v14z"/><path d="M45 44c4-2 8-2 12 0" stroke-width="2.4"/><circle cx="66" cy="38" r="2.6" fill="#00000066" stroke="none"/>',
    "b": '<path d="M50 13c9 9 13 16 13 23a13 13 0 1 1-26 0c0-7 4-14 13-23z"/><path d="M50 19v17" stroke-width="3"/><rect x="36" y="53" width="28" height="7" rx="3.5"/><path d="M34 60h32l-3 13H37z"/><path d="M30 73h40l4 11H26z"/>',
    "q": '<circle cx="24" cy="30" r="5"/><circle cx="37" cy="21" r="5"/><circle cx="50" cy="17" r="5"/><circle cx="63" cy="21" r="5"/><circle cx="76" cy="30" r="5"/><path d="M24 34l9 22h34l9-22-15 12-11-19-11 19z"/><path d="M33 56h34l-3 14H36z"/><path d="M27 70h46l5 14H22z"/>',
    "k": '<path d="M45 11h10v8h8v10h-8v8H45v-8h-8V19h8z"/><path d="M33 37h34l4 21H29z"/><path d="M31 58h38l-4 15H35z"/><path d="M26 73h48l5 12H21z"/>',
}
FILL = {"w": "#fbfbfb", "b": "#3b3936"}
EDGE = {"w": "#4c4a47", "b": "#141312"}


def svg_for(kind: str, color: str) -> bytes:
    body = PIECES[kind]
    fill, edge = FILL[color], EDGE[color]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            f'<g fill="{fill}" stroke="{edge}" stroke-width="2.6" '
            f'stroke-linejoin="round" stroke-linecap="round">{body}</g></svg>').encode()


# --------------------------------------------------------------------------
# engine install (progress window)
# --------------------------------------------------------------------------
SF_BASE = "https://github.com/official-stockfish/Stockfish/releases/latest/download/"
SF_URLS = [SF_BASE + name for name in (
    "stockfish-windows-x86-64-avx2.zip",
    "stockfish-windows-x86-64-sse41-popcnt.zip",
    "stockfish-windows-x86-64.zip",
    "stockfish-windows-x86-64-bmi2.zip",
)]


class Installer(QDialog):
    """Shown only when Stockfish is not on disk. Downloads it into
    %APPDATA%\\ChessCoach\\engine and then lets the app start."""

    done = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chess Coach — installing engine")
        self.setFixedSize(420, 190)
        self.setStyleSheet("QDialog{background:#262421;color:#e9e6e2;font:14px 'Segoe UI';}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        title = QLabel("Installing Stockfish")
        title.setStyleSheet("font:700 17px 'Segoe UI';color:#fff")
        self.status = QLabel("one moment…")
        self.status.setStyleSheet("color:#a09c96")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        self.bar.setStyleSheet(
            "QProgressBar{background:rgba(255,255,255,.08);border:none;border-radius:5px}"
            "QProgressBar::chunk{background:#81b64c;border-radius:5px}")
        lay.addWidget(title)
        lay.addWidget(self.status)
        lay.addWidget(self.bar)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        target = core.data_dir() / "engine" / "stockfish.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        last = "unknown error"
        for url in SF_URLS:
            try:
                self.status.setText("connecting…")
                req = urllib.request.Request(url, headers={"User-Agent": "chess-coach/2"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    total = int(r.headers.get("Content-Length") or 0)
                    buf = io.BytesIO()
                    read = 0
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        buf.write(chunk)
                        read += len(chunk)
                        if total:
                            self.bar.setValue(int(read * 100 / total))
                        self.status.setText(f"{read / 1_048_576:.1f} MB of "
                                            f"{total / 1_048_576:.1f} MB")
                buf.seek(0)
                with zipfile.ZipFile(buf) as z:
                    name = next(n for n in z.namelist() if n.lower().endswith(".exe"))
                    target.write_bytes(z.read(name))
                self.done.emit(True)
                self.accept()
                return
            except Exception as exc:                  # try the next asset name
                last = f"{type(exc).__name__}: {exc}"
                continue
        # every URL failed: leave the window open with the real reason
        self.status.setText("install failed — " + last[:110])
        self.setToolTip(last)
        self.bar.setStyleSheet(
            "QProgressBar{background:rgba(255,255,255,.08);border:none;border-radius:5px}"
            "QProgressBar::chunk{background:#ca3431;border-radius:5px}")
        self.done.emit(False)


# --------------------------------------------------------------------------
# bridge: worker threads -> Qt signals
# --------------------------------------------------------------------------
class Bridge(QObject):
    graded = Signal(object)     # one move graded: rec dict
    replied = Signal(object)    # Ray's chosen UCI move
    hinted = Signal(object)     # best_move() result
    evaluated = Signal(object)  # idle baseline eval
    failed = Signal(str)


BRIDGE = Bridge()


# --------------------------------------------------------------------------
# glass card
# --------------------------------------------------------------------------
class Card(QWidget):
    def __init__(self, radius=18, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.fillPath(path, GLASS)
        pen = QPen(STROKE, 1)
        p.setPen(pen)
        p.drawPath(path)
        # top highlight, the cheap version of a glass edge
        hi = QLinearGradient(0, 0, 0, 24)
        hi.setColorAt(0, QColor(255, 255, 255, 26))
        hi.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(hi)
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, 22), 16, 16)


# --------------------------------------------------------------------------
# board
# --------------------------------------------------------------------------
class Board(QWidget):
    clicked = Signal(int, int)      # from_square, to_square (-1 = select only)

    def __init__(self):
        super().__init__()
        self.board = chess.Board()
        self.player_color = chess.WHITE
        self.orientation = chess.WHITE
        self.selected = None
        self.legal = []
        self.last = None
        self.hint_from = None
        self.hint_to = None
        self.pre = None                  # (from_sq, to_sq) premove
        self.user_arrows = []            # right-drag arrows, persist through moves
        self.threats = True
        self.dots = True
        self.coords = True
        self.eval_on = True
        self.show_arrows = True
        self._rpress = None
        self.badge = None               # (square, cls)
        self.badge_at = 0.0
        self.anim = None                # (piece, color, p0, p1, t0, dest_sq)
        self._press = None
        self._moved = False
        self.last_drag = False
        self.pix = {}
        self.setMinimumSize(420, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    # ---------- geometry ----------
    def pad(self):
        return 14

    def cell(self):
        return (min(self.width(), self.height()) - self.pad() * 2) / 8

    def origin(self):
        c = self.cell()
        return ((self.width() - c * 8) / 2, (self.height() - c * 8) / 2)

    def square_rect(self, sq):
        c = self.cell()
        ox, oy = self.origin()
        f = chess.square_file(sq)
        r = chess.square_rank(sq)
        col = f if self.orientation == chess.WHITE else 7 - f
        row = 7 - r if self.orientation == chess.WHITE else r
        return QRectF(ox + col * c, oy + row * c, c, c)

    def at(self, x, y):
        c = self.cell()
        ox, oy = self.origin()
        if not (ox <= x < ox + c * 8 and oy <= y < oy + c * 8):
            return None
        col = int((x - ox) // c)
        row = int((y - oy) // c)
        f = col if self.orientation == chess.WHITE else 7 - col
        r = 7 - row if self.orientation == chess.WHITE else row
        return chess.square(f, r)

    def tick(self):
        if self.anim:
            if time.time() - self.anim[4] > 0.17:
                self.anim = None
            self.update()
        if self.badge and time.time() - self.badge_at > 3.2:
            self.badge = None
            self.update()

    # ---------- pieces ----------
    def pixmap(self, piece, size):
        key = (piece.symbol(), int(size))
        if key not in self.pix:
            kind = piece.symbol().lower()
            color = "w" if piece.color else "b"
            r = QSvgRenderer(svg_for(kind, color))
            pm = QPixmap(int(size), int(size))
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            r.render(p)
            p.end()
            self.pix[key] = pm
        return self.pix[key]

    # ---------- painting ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        c = self.cell()
        ox, oy = self.origin()

        # frame shadow + board
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRoundedRect(QRectF(ox - 8, oy - 6, c * 8 + 16, c * 8 + 18), 10, 10)

        for sq in chess.SQUARES:
            r = self.square_rect(sq)
            light = (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1
            p.fillRect(r, LIGHT if light else DARK)

        if self.last:
            for sq in self.last:
                p.fillRect(self.square_rect(sq), LAST)
        if self.pre:
            red = QColor(214, 64, 52, 115)
            for sq in self.pre:
                p.fillRect(self.square_rect(sq), red)
        if self.selected is not None:
            p.fillRect(self.square_rect(self.selected), SEL)
        if self.hint_from is not None:
            p.setPen(QPen(HINT, 4))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.square_rect(self.hint_from).adjusted(2, 2, -2, -2))

        king = self.board.king(self.board.turn) if self.board.is_check() else None
        if king is not None:
            r = self.square_rect(king)
            g = QRadialGradient(r.center(), c * 0.62)
            g.setColorAt(0, QColor(221, 90, 90, 235))
            g.setColorAt(1, QColor(221, 90, 90, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(r.center(), c * 0.62, c * 0.62)

        if self.selected is not None and self.dots:
            p.setPen(Qt.NoPen)
            for mv in self.legal:
                r = self.square_rect(mv.to_square)
                if self.board.piece_at(mv.to_square):
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(QColor(20, 20, 20, 70), 5))
                    p.drawEllipse(r.adjusted(4, 4, -4, -4))
                else:
                    p.setBrush(QColor(20, 20, 20, 58))
                    p.drawEllipse(r.center(), c * 0.15, c * 0.15)

        # pieces (skip the one in flight)
        moving = self.anim[5] if self.anim else None
        for sq in chess.SQUARES:
            piece = self.board.piece_at(sq)
            if piece is None or (moving and sq == moving):
                continue
            r = self.square_rect(sq)
            pm = self.pixmap(piece, c * 0.94)
            p.drawPixmap(r.center() - QPointF(pm.width() / 2, pm.height() / 2), pm)

        if self.anim:
            piece, _color, p0, p1, t0, _sq = self.anim
            k = min(1.0, (time.time() - t0) / 0.17)
            k = 1 - (1 - k) ** 3
            pos = p0 + (p1 - p0) * k
            pm = self.pixmap(piece, c * 0.94)
            p.drawPixmap(pos - QPointF(pm.width() / 2, pm.height() / 2), pm)

        if self.show_arrows:
            for a, b in self.user_arrows:
                self._arrow(p, a, b, QColor("#e8a13a"), 5)
            if self.threats:
                self._threats(p)
        if self.hint_to is not None:
            self._arrow(p, self.hint_from, self.hint_to)
        if self.coords:
            p.setPen(QColor(40, 60, 30, 170))
            f = QFont("Consolas", max(6, int(c * 0.12)))
            p.setFont(f)
            for i in range(8):
                p.drawText(QRectF(ox + i * c, oy + 8 * c - c * 0.2, c, c * 0.2),
                           Qt.AlignRight, chr(ord('a') + i))
                p.drawText(QRectF(ox + 3, oy + (7 - i) * c + 2, c * 0.2, c * 0.2),
                           Qt.AlignLeft, str(i + 1))

        if self.badge:
            sq, cls = self.badge
            r = self.square_rect(sq)
            size = c * 0.46
            # bottom-left of the icon on the square's top-right corner
            box = QRectF(r.right() - 6, r.top() - size + 6, size, size)
            pm = self.badge_pixmap(cls, size)
            if not pm.isNull():
                clip = QPainterPath()
                clip.addEllipse(box)
                p.setClipPath(clip)
                p.drawPixmap(box.topLeft(), pm)
                p.setClipping(False)

    def badge_pixmap(self, cls, size):
        key = (cls, int(size))
        if key not in self.pix:
            path = HERE.parent / "assets" / "badges" / f"{cls}.png"
            pm = QPixmap(str(path))
            if not pm.isNull():
                pm = pm.scaled(int(size), int(size), Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
            self.pix[key] = pm
        return self.pix[key]

    def _threats(self, p):
        """Red arrows: their pieces attacking ours, worst victim first."""
        enemy = not self.player_color
        if self.board.turn != enemy:
            return
        found = []
        for sq in chess.SQUARES:
            piece = self.board.piece_at(sq)
            if not piece or piece.color != enemy:
                continue
            for target in self.board.attacks(sq):
                victim = self.board.piece_at(target)
                if victim and victim.color == self.player_color:
                    found.append((victim.piece_type, sq, target))
        found.sort(key=lambda t: t[0], reverse=True)
        for _kind, a, b in found[:3]:
            self._arrow(p, a, b, QColor(221, 90, 90, 150), 4)

    def _arrow(self, p, a, b, color=None, width=None):
        if a is None or b is None:
            return
        ra, rb = self.square_rect(a), self.square_rect(b)
        c = self.cell()
        start, end = ra.center(), rb.center()
        v = end - start
        length = (v.x() ** 2 + v.y() ** 2) ** 0.5 or 1
        u = QPointF(v.x() / length, v.y() / length)
        tip = end - u * (c * 0.28)
        p.setPen(QPen(color or ARROW, width or max(4, c * 0.075),
                      Qt.SolidLine, Qt.RoundCap))
        p.drawLine(start + u * (c * 0.3), tip)
        left = tip + QPointF(-u.y(), u.x()) * (c * 0.14)
        right = tip + QPointF(u.y(), -u.x()) * (c * 0.14)
        head = QPainterPath(tip + u * (c * 0.16))
        head.lineTo(left)
        head.lineTo(right)
        head.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(color or ARROW)
        p.drawPath(head)

    # ---------- input ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._rpress = self.at(e.position().x(), e.position().y())
            return
        self._moved = False
        self._press = self.at(e.position().x(), e.position().y())
        if self._press is not None:
            self.clicked.emit(self._press, -1)

    def mouseMoveEvent(self, e):
        # A press that travels to another square is a drag: the piece is under
        # the cursor already, so it must snap instead of sliding from its origin.
        if self._press is not None:
            here = self.at(e.position().x(), e.position().y())
            if here is not None and here != self._press:
                self._moved = True
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            start = self._rpress
            self._rpress = None
            end = self.at(e.position().x(), e.position().y())
            if start is not None and end is not None and end != start:
                pair = (start, end)
                if pair in self.user_arrows:
                    self.user_arrows.remove(pair)
                else:
                    self.user_arrows.append(pair)
                self.update()
            return
        start = getattr(self, "_press", None)
        self._press = None
        self.last_drag = getattr(self, "_moved", False)
        if start is None:
            return
        end = self.at(e.position().x(), e.position().y())
        if end is not None and end != start:
            self.clicked.emit(start, end)


    def select(self, sq, legal):
        self.selected = sq
        self.legal = legal
        self.update()

    def clear(self):
        self.selected = None
        self.legal = []
        self.update()


# --------------------------------------------------------------------------
# small widgets
# --------------------------------------------------------------------------
class EvalBar(QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0
        self.setFixedWidth(18)

    def set(self, cp):
        self.value = cp
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 7, 7)
        p.fillPath(path, QColor("#161814"))
        frac = max(-1.0, min(1.0, self.value / 700))
        white_h = r.height() * (frac + 1) / 2
        clip = QPainterPath()
        clip.addRoundedRect(r, 7, 7)
        p.setClipPath(clip)
        p.fillRect(QRectF(r.left(), r.bottom() - white_h, r.width(), white_h),
                   QColor("#e9e6e2"))
        p.setClipping(False)
        p.setPen(QPen(STROKE, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class Chip(QLabel):
    def __init__(self):
        super().__init__("?")
        self.setFixedSize(34, 34)
        self.setAlignment(Qt.AlignCenter)
        self.set_color("#3b3936")

    def set_color(self, color):
        self.setStyleSheet(
            f"background:{color};color:#fff;border-radius:17px;"
            f"border:2px solid rgba(0,0,0,.38);"
            f"font:800 12px 'Consolas';")

    def show_cls(self, cls):
        self.setText(SYM.get(cls, "?"))
        self.set_color(core.COL.get(cls, "#3b3936"))


def button(text, primary=False):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(38)
    if primary:
        b.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #93c95f,stop:1 #6fa83d);color:#12280d;border:none;"
            "border-radius:12px;font:700 13px 'Segoe UI';}"
            "QPushButton:hover{background:#8fc45a;}"
            "QPushButton:pressed{background:#5f9133;}")
    else:
        b.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,.08);color:#e9e6e2;"
            "border:1px solid rgba(255,255,255,.12);border-radius:12px;"
            "font:600 13px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,255,255,.15);}"
            "QPushButton:pressed{background:rgba(255,255,255,.06);}")
    return b


# --------------------------------------------------------------------------
# main window
# --------------------------------------------------------------------------
class Window(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setWindowTitle("Chess Coach")
        self.resize(1180, 860)
        self.setMinimumSize(980, 720)

        self.board = chess.Board()
        self.player_color = chess.WHITE
        self.orientation = chess.WHITE
        self.stack = []                 # {move, san, fen, cls, loss, only, best, color}
        self.pre_eval = 0
        self.last_eval = 0
        self.busy = False
        self.hint_armed = False
        self.hint_cache = None
        self.hint_info = None
        self.premove = None
        self.game_over = False

        root = QWidget()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(22, 22, 22, 22)
        lay.setSpacing(18)

        # left: header + board + eval
        left = QVBoxLayout()
        left.setSpacing(12)
        head = Card(20)
        head.setFixedHeight(74)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 12)
        icon = QLabel()
        icon.setPixmap(QIcon(str(HERE.parent / "assets" / "icon-256.png")).pixmap(40, 40))
        hl.addWidget(icon)
        titles = QVBoxLayout()
        t1 = QLabel("CHESS COACH")
        t1.setStyleSheet("font:800 15px 'Segoe UI';letter-spacing:2px;color:#fff")
        t2 = QLabel("play · learn · Ray")
        t2.setStyleSheet("color:#a09c96;font:12px 'Segoe UI'")
        titles.addWidget(t1)
        titles.addWidget(t2)
        hl.addLayout(titles)
        hl.addStretch(1)
        self.turn = QLabel("your move")
        self.turn.setStyleSheet("color:#cfd6c9;font:600 12px 'Segoe UI'")
        hl.addWidget(self.turn)
        left.addWidget(head)

        row = QHBoxLayout()
        self.evalbar = EvalBar()
        row.addWidget(self.evalbar)
        self.boardw = Board()
        self.boardw.clicked.connect(self.on_board)
        row.addWidget(self.boardw, 1)
        left.addLayout(row, 1)
        lay.addLayout(left, 1)

        # right panel
        right = QVBoxLayout()
        right.setSpacing(12)
        right.setContentsMargins(0, 0, 0, 0)

        ray = Card()
        rl = QVBoxLayout(ray)
        rl.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        who = QLabel("Ray")
        who.setStyleSheet("font:800 14px 'Segoe UI';color:#fff")
        self.lvl = QLabel("CASUAL")
        self.lvl.setStyleSheet("background:#81b64c;color:#12280d;border-radius:7px;"
                               "padding:3px 7px;font:800 10px 'Segoe UI'")
        self.eng = QLabel("engine ready")
        self.eng.setStyleSheet("color:#8c9a86;font:700 9px 'Consolas'")
        top.addWidget(who)
        top.addWidget(self.lvl)
        top.addStretch(1)
        top.addWidget(self.eng)
        rl.addLayout(top)
        self.say = QLabel("your move. i'm not a pro — i'm just loud.")
        self.say.setWordWrap(True)
        self.say.setMinimumHeight(70)
        self.say.setStyleSheet(
            "background:rgba(12,16,12,.42);border:1px solid rgba(255,255,255,.08);"
            "border-radius:14px;padding:10px;color:#e6e9e2;font:13px 'Segoe UI'")
        rl.addWidget(self.say)
        right.addWidget(ray)

        rate = Card()
        rr = QHBoxLayout(rate)
        rr.setContentsMargins(14, 12, 14, 12)
        self.chip = Chip()
        self.rate = QLabel("make a move — i'll rate it")
        self.rate.setStyleSheet("color:#c9cfc4;font:12px 'Segoe UI'")
        rr.addWidget(self.chip)
        rr.addWidget(self.rate, 1)
        right.addWidget(rate)

        controls = Card()
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(14, 14, 14, 14)
        b1 = QHBoxLayout()
        self.b_hint = button("💡  Hint")
        self.b_undo = button("↶  Revert")
        self.b_flip = button("⇅  Flip")
        for b in (self.b_hint, self.b_undo, self.b_flip):
            b1.addWidget(b)
        b2 = button("New game", True)
        cl.addLayout(b1)
        cl.addWidget(b2)
        right.addWidget(controls)
        self.b_hint.clicked.connect(self.hint)
        self.b_undo.clicked.connect(self.undo)
        self.b_flip.clicked.connect(self.flip)
        b2.clicked.connect(self.new_game)

        moves_card = Card()
        ml = QVBoxLayout(moves_card)
        ml.setContentsMargins(10, 10, 10, 10)
        self.moves = QLabel("no moves yet")
        self.moves.setAlignment(Qt.AlignTop)
        self.moves.setWordWrap(True)
        self.moves.setStyleSheet("background:rgba(12,16,12,.35);border-radius:12px;"
                                 "padding:10px;color:#e9e6e2;font:600 13px 'Segoe UI'")
        self.moves.setMinimumHeight(150)
        ml.addWidget(self.moves)
        right.addWidget(moves_card)

        diff = Card()
        dl = QVBoxLayout(diff)
        dl.setContentsMargins(14, 12, 14, 12)
        dh = QHBoxLayout()
        lab = QLabel("Difficulty")
        lab.setStyleSheet("color:#a09c96;font:12px 'Segoe UI'")
        self.dval = QLabel("casual · 5")
        self.dval.setStyleSheet("color:#fff;font:700 12px 'Consolas'")
        dh.addWidget(lab)
        dh.addStretch(1)
        dh.addWidget(self.dval)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 20)
        self.slider.setValue(5)
        self.slider.valueChanged.connect(self.on_diff)
        dl.addLayout(dh)
        dl.addWidget(self.slider)
        self.hopeless = QCheckBox("hopeless")
        self.hopeless.setStyleSheet("color:#8d96a8;font:600 11px 'Segoe UI'")
        dl.addWidget(self.hopeless)
        right.addWidget(diff)

        settings = Card()
        sl = QHBoxLayout(settings)
        sl.setContentsMargins(12, 8, 12, 8)
        for label, attr in (("Threats", "threats"), ("Dots", "dots"),
                            ("Coords", "coords"), ("Arrows", "show_arrows")):
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox{color:#cfd6c9;font:11px 'Segoe UI'}"
                             "QCheckBox::indicator{width:14px;height:14px;border-radius:4px;"
                             "background:rgba(255,255,255,.12)}"
                             "QCheckBox::indicator:checked{background:#81b64c}")
            cb.toggled.connect(lambda on, a=attr: self.set_flag(a, on))
            sl.addWidget(cb)
        right.addWidget(settings)

        chat = Card()
        chl = QHBoxLayout(chat)
        chl.setContentsMargins(12, 10, 12, 10)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("ask Ray something…")
        self.entry.setStyleSheet(
            "QLineEdit{background:rgba(12,16,12,.55);border:1px solid rgba(255,255,255,.1);"
            "border-radius:10px;padding:8px 10px;color:#e9e6e2;font:12px 'Segoe UI'}")
        send = button("Send")
        send.setFixedWidth(64)
        chl.addWidget(self.entry, 1)
        chl.addWidget(send)
        right.addWidget(chat)
        send.clicked.connect(self.send_chat)
        self.entry.returnPressed.connect(self.send_chat)

        lay.addLayout(right)

        BRIDGE.graded.connect(self.after_grade)
        BRIDGE.replied.connect(self.after_reply)
        BRIDGE.hinted.connect(self.after_hint)
        BRIDGE.evaluated.connect(self.after_baseline)
        BRIDGE.failed.connect(self.after_fail)

        # a search that never comes back must never leave "thinking" up forever
        self._think_started = 0.0
        self._watch = QTimer(self)
        self._watch.timeout.connect(self.watch_think)
        self._watch.start(1000)

        self.new_game(first=True)

    def watch_think(self):
        if not self.busy:
            self._think_started = 0.0
            return
        now = time.time()
        if not self._think_started:
            self._think_started = now
            return
        if now - self._think_started < 12.0:
            return
        self._think_started = 0.0
        self.busy = False
        self.eng.setText("engine stalled")
        self.say_text("the engine stopped answering — i'm waking it up.")
        if not self.engine.alive():
            threading.Thread(target=self._revive, daemon=True).start()

    def _revive(self):
        ok = self.engine.restart()
        BRIDGE.failed.emit("" if ok else "engine restart failed")

    def after_fail(self, why):
        """A worker died: release the board and say why, never sit on thinking."""
        self._think_started = 0.0
        self.busy = False
        self.eng.setText("engine ready" if not why else "engine failed")
        if why:
            self.say_text(f"engine hiccup: {why[:120]}")

    # ---------- helpers ----------
    def paint_bg(self):
        pass

    def resizeEvent(self, e):
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, BG1)
        g.setColorAt(1, BG0)
        p.fillRect(self.rect(), g)
        glow = QRadialGradient(self.width() * 0.15, -40, self.width() * 0.8)
        glow.setColorAt(0, QColor(118, 150, 86, 40))
        glow.setColorAt(1, QColor(118, 150, 86, 0))
        p.fillRect(self.rect(), glow)

    def difficulty(self):
        return self.slider.value()

    def refresh(self):
        b = self.boardw
        b.board = self.board
        b.orientation = self.orientation
        b.last = ((self.stack[-1]["move"].from_square, self.stack[-1]["move"].to_square)
                  if self.stack else None)
        b.update()
        self.evalbar.set(self.last_eval)
        side = "your move" if self.board.turn == self.player_color else "Ray is thinking…"
        self.turn.setText(f"{side}  ·  move {self.board.fullmove_number}  ·  "
                          f"eval {core.fmt_eval(self.last_eval)}")
        rows = []
        for i in range(0, len(self.stack), 2):
            n = i // 2 + 1
            left = self.stack[i]
            right = self.stack[i + 1] if i + 1 < len(self.stack) else None
            rows.append(f"{n}.  {self.fig(left['san'])}{self.glyph(left)}"
                        + (f"      {self.fig(right['san'])}{self.glyph(right)}" if right else ""))
        self.moves.setText("\n".join(rows) if rows else "no moves yet")

    @staticmethod
    def fig(san):
        table = {"N": "♞", "B": "♝", "R": "♜", "Q": "♛", "K": "♚"}
        return table.get(san[:1], "") + (san[1:] if san[:1] in table else san)

    @staticmethod
    def glyph(rec):
        if not rec.get("cls"):
            return ""
        return "  " + SYM.get(rec["cls"], "")

    def say_text(self, text):
        self.say.setText(text)

    def after_baseline(self, payload):
        fen, cp = payload
        if fen != self.board.fen():
            return                    # the board moved on while we searched
        self.pre_eval = cp
        self.last_eval = cp
        self.refresh()

    # ---------- game ----------
    def new_game(self, first=False):
        if not first and self.stack and not self.game_over:
            pass
        self.board = chess.Board()
        self.stack = []
        self.player_color = chess.WHITE
        self.orientation = chess.WHITE
        self.boardw.hint_from = self.boardw.hint_to = None
        self.boardw.pre = None
        self.premove = None
        self.boardw.badge = None
        self.boardw.clear()
        self.game_over = False
        self.busy = False
        self.hint_armed = False
        self.hint_cache = None
        self.pre_eval = 0
        self.last_eval = 0
        self.chip.setText("?")
        self.chip.set_color("#3b3936")
        self.rate.setText("make a move — i'll rate it")
        self.say_text("your move. i'm not a pro — i'm just loud.")
        self.refresh()
        self.engine.new_game()
        self.prefetch_baseline()

    def prefetch_baseline(self):
        fen = self.board.fen()
        def work():
            cp, _, _ = self.engine.analyze(fen, 12)
            BRIDGE.evaluated.emit((fen, cp))
        threading.Thread(target=work, daemon=True).start()

    def on_board(self, a, b):
        if self.game_over:
            return
        if b == -1:
            # selecting is always allowed, even while Ray thinks
            probe = self.board
            if self.board.turn != self.player_color:
                probe = self.board.copy(stack=False)
                probe.turn = self.player_color
            piece = probe.piece_at(a)
            if piece and piece.color == self.player_color:
                self.boardw.select(a, [m for m in probe.legal_moves
                                       if m.from_square == a])
            else:
                self.boardw.clear()
            return
        if self.busy:
            return
        if self.board.turn != self.player_color:
            # premove: validate against a copy of the board with our side to move
            probe = self.board.copy(stack=False)
            probe.turn = self.player_color
            mv = next((m for m in probe.legal_moves
                       if m.from_square == a and m.to_square == b), None)
            if mv is None:
                return
            self.premove = None if self.premove == (a, b) else (a, b)
            self.boardw.pre = self.premove
            self.boardw.clear()
            self.boardw.update()
            self.say_text("premove set." if self.premove else "premove cancelled.")
            return
        if b == -1:
            piece = self.board.piece_at(a)
            if piece and piece.color == self.player_color:
                self.boardw.select(a, [m for m in self.board.legal_moves
                                       if m.from_square == a])
            else:
                self.boardw.clear()
            return
        move = next((m for m in self.board.legal_moves
                     if m.from_square == a and m.to_square == b), None)
        if move is None:
            return
        if move.promotion and move.promotion != chess.QUEEN:
            move = chess.Move(a, b, promotion=chess.QUEEN)
        self.play(move, animate=not getattr(self.boardw, "last_drag", False))

    def play(self, move, ray=False, animate=True):
        fen_before = self.board.fen()
        only = self.board.legal_moves.count() == 1
        san = self.board.san(move)
        color = self.board.turn
        before = self.boardw.square_rect(move.from_square).center()
        self.board.push(move)
        after = self.boardw.square_rect(move.to_square).center()
        # drag: the piece is already at the cursor, so it snaps. click: it slides.
        self.boardw.anim = ((self.board.piece_at(move.to_square), color, before, after,
                             time.time(), move.to_square) if animate else None)
        rec = {"move": move, "san": san, "fen": fen_before, "cls": None,
               "loss": 0, "only": only, "color": color, "best": None,
               "eval": None}
        self.stack.append(rec)
        self.boardw.clear()
        self.boardw.badge = None
        self.refresh()
        self.busy = True
        self.eng.setText("thinking…")

        if ray:
            threading.Thread(target=self._ray_move, args=(rec,), daemon=True).start()
        else:
            threading.Thread(target=self._grade, args=(rec,), daemon=True).start()

    # ---------- engine threads ----------
    def _grade(self, rec):
        try:
            g = self.engine.grade(rec["fen"], rec["move"].uci(), core.GRADE_DEPTH)
            best, played = g["best"], g["played"] or g["best"]
            sign = 1 if rec["color"] == chess.WHITE else -1
            best_cp = sign * best["cp"]
            played_cp = sign * played["cp"]
            loss = max(0, best_cp - played_cp)
            is_best = best["uci"] == rec["move"].uci()
            second = g["second"]
            gap = max(0, best_cp - sign * second["cp"]) if second else 0
            sans = [r["san"] for r in self.stack]
            cls = core.grade_move(loss=loss, is_best=is_best, gap=gap, best_cp=best_cp,
                                  played_cp=played_cp, best_mate=best["mate"],
                                  sac=core.is_sacrifice(chess.Board(rec["fen"]), rec["move"]),
                                  book=tuple(sans) in core.BOOK_MOVES, only=rec["only"])
            rec.update({"cls": cls, "loss": loss, "best": best["uci"], "eval": played_cp})
            BRIDGE.graded.emit(rec)
        except Exception as exc:
            BRIDGE.failed.emit(str(exc))

    def _ray_move(self, rec):
        level = 0 if self.hopeless.isChecked() else self.difficulty()
        uci = core.book_move(self.board.fen()) if level >= 3 else None
        if not uci:
            uci = self.engine.play(self.board.fen(), level,
                                   core.depth_for(level), budget_ms=1200)
        BRIDGE.replied.emit(uci)

    def after_grade(self, rec):
        self.busy = False
        self.eng.setText("engine ready")
        self.last_eval = rec["eval"] or 0
        self.chip.show_cls(rec["cls"])
        self.rate.setText(core.LABEL.get(rec["cls"], ""))
        self.boardw.badge = (rec["move"].to_square, rec["cls"])
        self.boardw.badge_at = time.time()
        self.refresh()
        self.say_text(self.ray_comment(rec))

        if self.board.is_game_over():
            self.game_over = True
            self.say_text(self.game_over_text())
            return
        threading.Thread(target=self._ray_move, args=(rec,), daemon=True).start()

    def after_reply(self, uci):
        """Apply Ray's move once. It must not schedule another Ray move."""
        self.eng.setText("engine ready")
        move = None
        if uci:
            try:
                candidate = chess.Move.from_uci(uci)
                if candidate in self.board.legal_moves:
                    move = candidate
            except ValueError:
                move = None
        if move is None:
            self.busy = False
            return

        fen_before = self.board.fen()
        san = self.board.san(move)
        color = self.board.turn
        p0 = self.boardw.square_rect(move.from_square).center()
        self.board.push(move)
        p1 = self.boardw.square_rect(move.to_square).center()
        self.boardw.anim = (self.board.piece_at(move.to_square), color, p0, p1, time.time(), move.to_square)
        self.stack.append({"move": move, "san": san, "fen": fen_before, "cls": "opp",
                           "loss": 0, "only": False, "color": color, "best": None,
                           "eval": None})
        self.boardw.badge = None
        self.boardw.clear()
        self.busy = False
        self.refresh()
        self.try_premove()
        if self.board.is_game_over():
            self.game_over = True
            self.say_text(self.game_over_text())
        else:
            self.prefetch_baseline()

    def ray_comment(self, rec):
        if not rec.get("cls"):
            return self.say.text()
        board = chess.Board(rec["fen"])
        move = rec["move"]
        piece = board.piece_at(move.from_square)
        captured = board.piece_at(move.to_square)
        if board.is_en_passant(move):
            captured = chess.Piece(chess.PAWN, not board.turn)
        sign = 1 if self.player_color == chess.WHITE else -1
        ctx = {
            "san": rec["san"],
            "from": chess.square_name(move.from_square),
            "to": chess.square_name(move.to_square),
            "piece": chess.piece_name(piece.piece_type) if piece else "piece",
            "captured": chess.piece_name(captured.piece_type) if captured else None,
            "check": board.gives_check(move),
            "castle": board.is_castling(move),
            "promo": bool(move.promotion),
            "cls": rec["cls"], "only": rec["only"],
            "loss": rec["loss"], "loss_text": f"{rec['loss'] / 100:.2f}",
            "best_san": self.best_san(rec), "best_to": (rec["best"] or "")[2:4] or None,
            "streak": 0,
        }
        return core.RAY.comment(ctx)

    def best_san(self, rec):
        if not rec.get("best"):
            return None
        try:
            return chess.Board(rec["fen"]).san(chess.Move.from_uci(rec["best"]))
        except Exception:
            return None

    def game_over_text(self):
        if self.board.is_checkmate():
            return ("checkmate. you got me." if self.board.turn != self.player_color
                    else "checkmate. that's you done — i've been there.")
        return "draw. fine by me, i was getting nervous."

    # ---------- actions ----------
    def hint(self):
        if self.busy or self.game_over or self.board.turn != self.player_color:
            return
        if self.hint_armed and self.hint_cache:
            move = chess.Move.from_uci(self.hint_cache)
            self.boardw.hint_to = move.to_square
            self.say_text(f"fine — {self.board.san(move)}. i'm no grandmaster, but i'd play that.")
            self.hint_armed = False
            self.hint_cache = None
            self.boardw.update()
            return
        fen = self.board.fen()
        book = core.book_move(fen)
        if book:
            self.hint_cache = book
            self.hint_armed = True
            self.boardw.hint_from = chess.Move.from_uci(book).from_square
            self.boardw.hint_to = None
            self.boardw.update()
            self.say_text("book move. look at that piece — press hint again to name it.")
            return
        self.say_text("thinking properly this time…")
        def work():
            info = self.engine.best_move(fen, 1500)
            BRIDGE.hinted.emit(info)
        threading.Thread(target=work, daemon=True).start()

    def after_hint(self, info):
        if not info or not info.get("uci"):
            self.say_text("i've got nothing. worrying for both of us.")
            return
        move = chess.Move.from_uci(info["uci"])
        self.hint_cache = info["uci"]
        self.hint_armed = True
        self.boardw.hint_from = move.from_square
        self.boardw.hint_to = None
        self.boardw.update()
        self.say_text("look at that piece. press hint again to just say it.")

    def try_premove(self):
        if not self.premove:
            return
        a, b = self.premove
        self.premove = None
        self.boardw.pre = None
        move = next((m for m in self.board.legal_moves
                     if m.from_square == a and m.to_square == b), None)
        if move:
            self.play(move)
        else:
            self.boardw.update()

    def undo(self):
        if self.busy or not self.stack:
            return
        plies = 2 if len(self.stack) >= 2 else 1
        for _ in range(plies):
            self.board.pop()
            self.stack.pop()
        self.boardw.anim = None
        self.boardw.badge = None
        self.game_over = False
        self.refresh()
        self.say_text("took it back. we don't talk about it.")
        self.prefetch_baseline()

    def flip(self):
        self.orientation = not self.orientation
        self.refresh()

    def set_flag(self, attr, on):
        setattr(self.boardw, attr, on)
        self.boardw.update()

    def on_diff(self, v):
        names = ("beginner", "beginner", "beginner", "beginner", "casual", "casual",
                 "casual", "casual", "club", "club", "club", "club", "strong",
                 "strong", "strong", "strong", "ruthless", "ruthless", "ruthless",
                 "ruthless", "ruthless")
        self.dval.setText(f"{names[v]} · {v}")
        self.lvl.setText(names[v].upper())

    def send_chat(self):
        text = self.entry.text().strip()
        if not text:
            return
        self.entry.clear()
        low = text.lower()
        if "hint" in low:
            self.hint()
            return
        if "why" in low or "explain" in low:
            self.say_text("stockfish says so and it has never once been polite about it.")
            return
        if low.startswith(("hi", "hey", "hello", "yo")):
            self.say_text(f"hey. {core.fmt_eval(self.last_eval)} on the board. let's play.")
            return
        if "undo" in low or "take back" in low:
            self.say_text("hit revert. i won't tell anyone.")
            return
        if "accuracy" in low or "rating" in low or "elo" in low:
            self.say_text("elo is a lie. play a few moves and i'll tell you about accuracy.")
            return
        self.say_text("i only know chess and your mistakes. mostly your mistakes.")


# --------------------------------------------------------------------------
def main():
    exe = core.engine_path()
    if exe is None:
        app = QApplication(sys.argv)
        dlg = Installer()
        ok = {"value": False}
        dlg.done.connect(lambda good: ok.update(value=good))
        dlg.exec()
        if not ok["value"]:
            return 1
        exe = core.engine_path()
        if exe is None:
            return 1
    else:
        app = QApplication(sys.argv)

    app.setApplicationName("Chess Coach")
    engine = core.Engine(exe)
    win = Window(engine)
    win.show()
    code = app.exec()
    engine.close()
    return code


if __name__ == "__main__":
    sys.exit(main())