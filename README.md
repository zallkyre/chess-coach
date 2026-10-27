![Chess Coach](assets/banner-full.jpg)

# Chess Coach

A native desktop chess app that grades every move the moment you play it,
draws what your opponent is threatening, and talks to you like a club player
instead of a GM.

Python + Tkinter. No browser, no webview, no JavaScript. Stockfish runs as a
real local process and answers in about 30ms.

Stockfish does the analysis. Ray does the talking. Ray is not a pro, he says so.

## Install

```powershell
irm https://raw.githubusercontent.com/zallkyre/chess-coach/main/install.ps1 | iex
```

That one line installs the app (~35 MB) to `%LOCALAPPDATA%\ChessCoach`, then
fetches Stockfish straight from the official Stockfish release, puts a
**Chess Coach** shortcut on your desktop, and launches it.

The engine is not bundled: it is a 98 MB NNUE binary, so it comes from
[official-stockfish/Stockfish](https://github.com/official-stockfish/Stockfish/releases)
at install time. That keeps the app download at 35 MB instead of 112 MB.

Uninstall: delete `%LOCALAPPDATA%\ChessCoach` and the desktop shortcut. Nothing
else is touched.

## What it does

- **Instant move rating**: Best / Excellent / Good / Inaccuracy / Mistake /
  Blunder, the pawns it cost you, and a colored chip
- **Threat arrows**: real legal attack generation, not a guess. Enemy pieces
  that can take something of yours get a red arrow, worst victim first
- **Hint**: first press points at the piece, second press names the move
- **Revert**: the button, or click any move in the list to rewind there
- **Drag or click** to move, legal-move dots, last-move and check highlights
- **Post-game review**: re-analyzes every position deeper than the live pass,
  fills in the whole move list, and gives you an accuracy number
- **Captured material**, board themes, coordinates, eval bar, all toggleable

## Keys and providers

Keys live in the [`secrets-mcp`](https://github.com/zallkyre/secrets-mcp) vault
(Windows DPAPI, bound to your user). The app reads them through the vault
library, so a key is never written to a file, a config, or a transcript.

Add or change one in the app: **key...** next to the chat box. Paste it, hit
Save. It comes back as a fingerprint (`…M98A`), never as a value.

| provider | default model | key needed |
|---|---|---|
| Groq | `openai/gpt-oss-20b` | yes |
| OpenAI | `gpt-4o-mini` | yes |
| OpenRouter | `openai/gpt-oss-20b` | yes |
| Ollama | `llama3.1` | no, local |

Any OpenAI-compatible endpoint works. Free Groq keys:
<https://console.groq.com/keys>

## How the grading works

Two things make the ratings trustworthy:

1. **Analysis always runs at Skill Level 20.** Stockfish's lowered skill levels
   deliberately fuzz the score they report, which turns shallow noise into fake
   mistakes. Weakness belongs in move selection, not in the evaluator.
2. **A noise floor (25cp)** and depth-based searches during play. A 4-pawn swing
   is a blunder; a 0.15-pawn wobble between two searches is not.

Live grading is depth 14 in roughly 30ms. The post-game review runs depth 18
over every position, which is where accuracy comes from.

## Stack

- `app.py`: the whole app. Tkinter UI, canvas board, PIL-drawn pieces, grading
- `engine/stockfish.exe`: native Stockfish, not in git, fetched at install
- `python-chess`: rules, legal moves, FEN, SAN
- `secrets_mcp`: DPAPI vault for API keys
- `index.html` / `proxy.py`: the older browser build, still in the repo

## Dev

```powershell
python app.py          # needs engine/stockfish.exe
```

Build the app:

```powershell
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name ChessCoach `
  --icon assets/icon.ico --add-data "assets;assets" `
  --paths ..\secrets-mcp --hidden-import secrets_mcp --hidden-import secrets_mcp.store `
  app.py
```

`engine/stockfish.exe` is not in git. Grab
`stockfish-windows-x86-64-universal.exe` from the official releases, rename it,
drop it in `engine/`.

## Limitations

- Windows first (the vault uses DPAPI)
- Ray is a small model with a specific voice. He will confidently say something
  mildly wrong. Treat him as a friend at the board, not a second engine

## License

MIT for this app. Stockfish is GPLv3 and is used unmodified.

---

**LLM guide** — Browser chess with instant move grading, threat arrows, and a Groq-backed coach · Stack: Python · Entry: `app.py` · Run: `open index.html` · License: MIT-0 - no conditions, resale allowed
