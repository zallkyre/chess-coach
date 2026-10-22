![Chess Coach](assets/banner-full.jpg)

# Chess Coach

A native desktop chess app that grades every move the moment you play it,
draws what your opponent is threatening, and talks to you like a club player
instead of a GM.

Python + Tkinter. No browser, no webview, no JavaScript. Stockfish runs as a
real local process and answers in about 30ms.

Stockfish does the analysis. Ray does the talking. Ray is not a pro — he says so.

## Install

```powershell
irm https://raw.githubusercontent.com/zallkyre/chess-coach/main/install.ps1 | iex
```

That one line downloads the app, installs it to `%LOCALAPPDATA%\ChessCoach`,
puts a **Chess Coach** shortcut on your desktop, stores your API key in the
`secrets-mcp` vault, and launches it.

Uninstall: delete `%LOCALAPPDATA%\ChessCoach` and the desktop shortcut. Nothing
else is touched.

## What it does

- **Instant move rating** — Best / Excellent / Good / Inaccuracy / Mistake /
  Blunder, the pawns it cost you, and a badge on the square you moved to
- **Threat arrows** — real legal attack generation, not a guess. Enemy pieces
  that can take something of yours get a red arrow, worst victim first
- **Hint** — first press points at the piece, second press names the move
- **Revert** — the button, or click any move in the list to rewind there
- **Drag or click** to move, legal-move dots, last-move and check highlights
- **Ray plays** — off / your side / both sides, so you can watch him play himself
- **Post-game review** — re-analyses every position deeper than the live pass,
  fills in the whole move list, and gives you an accuracy number
- **Captured material**, board themes, coordinates, sound, eval bar — all
  toggleable in Settings

## Keys and providers

Keys live in the [`secrets-mcp`](https://github.com/zallkyre/secrets-mcp) vault
(Windows DPAPI, bound to your user). The app is served by a local Python process
that resolves the key per request, so **it never reaches the browser, the JS
bundle, the repo, or a transcript**.

Add or change a key in **Settings → Coach brain**: paste it, hit Save. It goes to
`127.0.0.1` only, gets stored in the vault, and comes back as a fingerprint
(`…M98A`), never as a value.

| provider | default model | key needed |
|---|---|---|
| Groq | `openai/gpt-oss-20b` | yes |
| OpenAI | `gpt-4o-mini` | yes |
| OpenRouter | `openai/gpt-oss-20b` | yes |
| Ollama | `llama3.1` | no, local |

Any OpenAI-compatible endpoint works — point the model field at whatever you
have. Free Groq keys: <https://console.groq.com/keys>

## How the grading works

Two things make the ratings trustworthy:

1. **Analysis always runs at Skill Level 20.** Stockfish's lowered skill levels
   deliberately fuzz the score they report, which turns shallow noise into fake
   mistakes. Weakness belongs in move selection, not in the evaluator.
2. **A noise floor (25cp)** and depth-based searches during play. A 4-pawn
   swing is a blunder; a 0.15-pawn wobble between two searches is not.

Live grading is depth 14 in roughly 30ms. The post-game review runs depth 18 over
every position with progress in the chip, which is where accuracy comes from.

## Stack

- `app.py` — the whole app: Tkinter UI, board drawing, grading, review
- `engine/stockfish.exe` — native Stockfish (not bundled in git, 98MB)
- `python-chess` — rules, legal moves, FEN, SAN
- `secrets_mcp` — DPAPI vault for API keys
- `index.html` / `proxy.py` — the older browser build, still in the repo

The desktop app talks to providers directly. Nothing listens on a port.

## Dev

```powershell
python proxy.py                      # http://127.0.0.1:8777
python launcher.py                   # same, in an app window
```

Build the app:

```powershell
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name ChessCoach `
  --icon assets/icon.ico --add-data "assets;assets" --add-data "engine;engine" `
  --paths ..\secrets-mcp --hidden-import secrets_mcp --hidden-import secrets_mcp.store `
  app.py
```

`engine/stockfish.exe` is not in git. Grab it from
[official-stockfish/Stockfish releases](https://github.com/official-stockfish/Stockfish/releases)
(`stockfish-windows-x86-64-universal.exe`), rename it, drop it in `engine/`.

Requires the `secrets-mcp` checkout next door for the vault code, or
`pip install secrets-mcp`.

## Limitations

- Windows first (the vault uses DPAPI)
- The engine is Stockfish 10 compiled to asm.js — correct, but not the fastest
- Ray is a small model with a specific voice. He will confidently say something
  mildly wrong. Treat him as a friend at the board, not a second engine

## License

MIT for this app. Stockfish is GPLv3 and is used unmodified in `vendor/`.
