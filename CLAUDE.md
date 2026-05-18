# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Goal

The tool's sole purpose is to give the user the strongest possible move suggestion on every turn, making them effectively unbeatable. It must never show moves when it is the opponent's turn, must use maximum Stockfish depth, and must derive its suggestion from the full session history — not just the latest screenshot in isolation.

## Project Overview
A macOS Python tool with a small always-on-top window containing two buttons. **Capture** screenshots the screen, detects a chess.com board, infers the move(s) played since the last capture via square-occupancy diff, feeds FEN into Stockfish, and shows the user's best move. **Infer** registers a game already in progress: it visually classifies every piece on the board so the user can start the assistant mid-game.

## Stack
- **UI / input**: `tkinter` buttons (no global keybind)
- **Screenshots**: `Pillow` / `pyautogui`
- **Board detection**: `opencv-python`
- **Chess logic / FEN**: `chess` (python-chess)
- **Engine**: `stockfish` (Python wrapper) — binary at `/opt/homebrew/bin/stockfish`
- **Overlay**: `tkinter` (built-in)
- **Piece icons**: PNG assets in `assets/pieces/` (e.g. `wN.png`, `bQ.png`)
- **Calibration image**: `assets/calibration_board.png` — a known starting-position chess.com board used at startup to extract piece templates that exactly match chess.com's rendering style

## Commands
```bash
# Install dependencies
pip install pillow pyautogui opencv-python python-chess stockfish

# Install Stockfish binary
brew install stockfish

# Verify Stockfish is available
stockfish --version

# Run (color required)
python main.py --color white
python main.py --color black

# Run all tests
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_engine.py
python -m pytest tests/test_pipeline.py
```

## Architecture

### Module responsibilities
| File | Role |
|---|---|
| `main.py` | Entry point; parses `--color`, instantiates overlay + controller, wires button callbacks |
| `screenshot.py` | Full-screen capture via `pyautogui`; strips alpha channel (macOS returns RGBA) so downstream code can assume 3-channel RGB |
| `board_detector.py` | HSV-based board locator (chess.com green) + `square_occupancy()` 8×8 std-dev grid |
| `frame_tracker.py` | Owns the `chess.Board`, multi-ply delta inference, duplicate-press detection, `last_suggestion` cache, and `infer_from_image()` |
| `piece_classifier.py` | Visual piece classification used by the **Infer** button; templates extracted at startup from `assets/calibration_board.png` |
| `engine.py` | Stockfish wrapper; accepts FEN, returns one best move. **Auto-restarts the Stockfish subprocess** on `StockfishException` (illegal positions can kill the child process), retries once, returns `None` if the retry also fails |
| `overlay.py` | 140×220px always-on-top tkinter window with Capture + Infer buttons and move display |

### User actions

There are exactly **two** user actions, both buttons on the overlay:

1. **Capture**: the normal per-turn action. Diff-based; cheap; never re-classifies pieces.
2. **Infer**: bootstrap from a position the assistant has not seen. Runs full visual piece classification (`piece_classifier.classify_board()`). Use when:
   - First launch and the game is already in progress, OR
   - The user lost track / pieces changed without Captures in between.

### Frame system (Capture button)
- **First Capture**: compute the square-occupancy bitmap and store as `prev_frame` baseline. The internal `chess.Board()` defaults to the standard starting position (or whatever Infer most recently set it to).
- **Subsequent Captures**: compute new occupancy → **compare to `prev_frame` first**:
  - **No change (duplicate press)**: do not push any move, do not call Stockfish, re-display `last_suggestion`. Log `print("Duplicate frame — no board change detected, re-showing last suggestion")`.
  - **Change detected**: diff which squares became empty vs. occupied → search legal-move sequences up to depth `MAX_INFERENCE_DEPTH` (currently 2 plies, so opponent-move-then-user-move works in a single Capture) → push the inferred move(s) → update `prev_frame`.
- The move suggestion is always computed against the **full `board` history**, not just the last two frames.
- "Last screenshots in the session" = the sequence of `prev_frame` bitmaps held in memory; nothing is written to disk.
- Cache the last Stockfish result as `last_suggestion: tuple[str, str] | None` (piece code + square label) in `frame_tracker.py`.
- A single `chess.Board()` object is the source of truth for move history; push moves onto it as they are inferred.

### Infer button — visual piece classification
- Screenshot → detect board → `piece_classifier.classify_board(board_img, player_color)`.
- Replaces `tracker.board` with the inferred position, resets `prev_frame` to the captured occupancy, clears `last_suggestion`.
- **Turn selection (`_set_valid_turn` in `frame_tracker.py`)**: prefer `player_color`, but if that produces an illegal position per `chess.Board.is_valid()` (e.g., the *opponent's* king is in check — meaning the user just delivered check and it's actually the opponent's turn), flip to the opponent's color. If neither turn is legal, refuse the Infer (return False) — do **not** mutate `tracker.board`. This is critical: feeding Stockfish an illegal "side-not-to-move in check" position crashes the engine subprocess.
- **Template source**: 12 templates (one per piece type per color) are extracted at startup from `assets/calibration_board.png` — a known starting position. This guarantees templates match chess.com's exact rendering style (the bundled `assets/pieces/` PNGs do not match closely enough for reliable matching).
- **Pixel-level pipeline per occupied square**: detect square background color from corner pixels → replace background pixels with neutral gray → pad to square, resize to `TEMPLATE_SIZE` → for the cell's color (white/black, decided by extreme-pixel count), template-match against the 6 same-color templates with `cv2.matchTemplate(TM_CCOEFF_NORMED)` and pick the highest score.
- Note: post-Infer, all subsequent Captures still use the cheap delta-based inference — classification is never re-run unless the user clicks Infer again.

### Turn gating — only show on user's turn
Before calling Stockfish on any frame, check `board.turn == player_color`:
- **User's turn**: compute best move → display in overlay normally.
- **Opponent's turn**: display `"..."` in the overlay; do not call Stockfish.

The overlay must **never** suggest a move for the opponent's side. (After Infer, `board.turn` is whatever `_set_valid_turn` chose — usually `player_color`, but flipped to the opponent if the user just delivered check. In that case the overlay correctly shows `"..."` because `is_user_turn()` returns False.)

### Board detection
- Use OpenCV HSV color range or template matching to locate the board rectangle.
- If board not found: increment a fail counter displayed as `(i)` above the overlay. Never crash.

### Overlay
- `140×220px`, no title bar, always on top.
- `overrideredirect(True)` + `wm_attributes('-topmost', True)` — must never block user input.
- Layout (top → bottom): fail counter, move display (piece icon + destination square), **Capture** button, **Infer** button.
- Positioned at right edge of screen; updated in-place (no new window per move).
- Buttons are the only input — there is no global keybind.

#### Overlay states

| State | Left cell | Right cell |
|---|---|---|
| User's turn, move found | Piece PNG icon | Square label (e.g. `e4`) |
| User's turn, no move inferred yet | — | `"?"` |
| Opponent's turn | — | `"..."` |
| Board not detected | — | `"(i)"` fail counter |
| Game over / illegal position | — | `"Game over"` |

## Constraints
- **Player color**: launch with `--color white` or `--color black` (argparse). If omitted, prompt `"Playing as [white/black]? "` in the terminal before starting the listener. Store as `player_color: chess.WHITE | chess.BLACK` in `main.py` and thread it through to `frame_tracker.py` and `overlay.py`. Color is never auto-detected from board orientation.
- Ask Stockfish for exactly **one** best move per frame — nothing more.
- Stockfish initialization: `{"Skill Level": 20, "Threads": 2, "Hash": 128}`. Call `get_best_move_time(minimum_thinking_time=500)` so the engine always finds the strongest reply.
- Board diff must be delta-based (empty-square comparison), never full visual piece classification on Frame 1+.
- Stockfish path: `/opt/homebrew/bin/stockfish`; fall back to `shutil.which("stockfish")`; `sys.exit(1)` with a clear message if missing.
- Do **not** call `subprocess` directly for Stockfish — use the `stockfish` Python package.
- Do **not** use `pygame`.
- All state is in-memory only — nothing written to disk between runs.
- `print()` only for logging — no logging framework unless explicitly requested.

## Error handling
| Condition | Behavior |
|---|---|
| Board not detected | Increment `(i)` counter in overlay, keep listening |
| Stockfish missing | `print` error + `sys.exit(1)` |
| Move cannot be inferred from delta | Show `"?"` in overlay, `print` warning, wait for next Capture |
| Screenshot fails | `print` error, do not update state |
| Duplicate Capture (no board change) | Re-display `last_suggestion`, log and continue |
| Infer fails to classify position | Show `"?"` in overlay, `print` warning, do not mutate `tracker.board` |
| Infer produces illegal position (no valid turn) | `print` warning, do not mutate `tracker.board`; user can retry |
| Stockfish process crashes | `engine.py` respawns the subprocess and retries once; on second failure returns `None` (overlay shows `?`). The next button click works normally — no relaunch needed |
| Stockfish returns `None` (game over) | Display `"Game over"` in overlay |

## Testing

### Test fixtures in `tests/`

| File | Purpose |
|---|---|
| `tests/debug_screenshot.png` | Full macOS screenshot — tests board detection (`board_detector.py`). Must return a non-None bounding rect that tightly wraps the chess.com board. |
| `tests/board.png` | Cropped board at starting position or early game — used as Frame 0 input for square-occupancy extraction. |
| `tests/board2.png` | Same board after one or more moves — used as Frame 1+ input. Diffing `board.png` → `board2.png` must yield the squares that changed and allow `frame_tracker.py` to infer a legal move. |
| `tests/debug_board.png` | Debug/intermediate crop — used for visual inspection during development. |

### Test files and known test cases

**`tests/test_engine.py`**
- `test_starting_position` — engine returns a legal move from the starting FEN.
- `test_mate_in_one` — engine finds the mating move when given a forced mate position.

**`tests/test_pipeline.py`**
- `test_board_detected_frame0` — `debug_screenshot.png` yields a non-None bounding rect.
- `test_board_detected_frame1` — board detection works on a cropped board image.
- `test_starting_position_empty_squares` — occupancy bitmap from `board.png` matches the standard starting layout.
- `test_frame0_sets_starting_fen` — first Capture initializes `chess.Board()` to the starting FEN.
- `test_frame1_empty_squares` — diffing `board.png` → `board2.png` produces the expected set of changed squares.
- `test_frame1_infers_e4` — multi-ply delta inference yields the move sequence `[e2e4, d7d5]`.
- `test_classify_starting_position` — `piece_classifier.classify_board()` on `board.png` reproduces the starting FEN.
- `test_classify_after_e4_d5` — classifier on `board2.png` reproduces the after-1.e4 d5 position.
- `test_infer_from_image_sets_turn` — `FrameTracker.infer_from_image()` installs the inferred position with turn = player_color.
- `test_infer_flips_turn_when_player_choice_illegal` — when the player's color produces an illegal position (opponent in check), `_set_valid_turn` flips to the opponent's color so Stockfish receives a legal FEN.
- `test_engine_responds_after_e4` — after the move sequence is pushed, Stockfish returns a legal reply.

### Test conventions
- Use `board.png` as Frame 0 and `board2.png` as Frame 1 when testing the diff/inference pipeline.
- Mock `engine.get_best_move()` in pipeline tests — tests must not require a running Stockfish binary.
- `test_engine.py` tests may call the real Stockfish binary; skip gracefully if it is not installed.
