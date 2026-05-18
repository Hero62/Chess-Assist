# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Goal

The tool's sole purpose is to give the user the strongest possible move suggestion on every turn, making them effectively unbeatable. It uses maximum Stockfish strength, polls the screen automatically while enabled, and only shows a suggestion when both (a) it is the user's turn and (b) the same position has been observed across consecutive polls — so transient screen states (piece animations, hover indicators) never produce a wrong-piece highlight.

## Project Overview

A macOS Python tool with a 150×140 always-on-top control panel (toggle + color switch) and on-screen highlight overlays drawn directly onto the chess.com board:

- **Red square outline** around the piece to move.
- **Red 40 %-opaque fill** over the destination square.

There are **no per-move buttons** — once the user clicks **START**, the program screenshots the screen every second, locates the board, classifies every piece visually, asks Stockfish for the best move, and renders the highlights. Clicking **STOP** hides them. The **WHITE / BLACK** switch under the toggle changes player orientation at runtime.

## Stack

- **UI**: `tkinter` (`Tk` + `Toplevel`s, `overrideredirect=True`, `MacWindowStyle floating noActivates`)
- **Screenshots**: `pyautogui` → `Pillow` → `CGWindowListCreateImage` (cursor never included on macOS)
- **Board detection**: `opencv-python` (HSV mask on chess.com dark-square green + connected-components)
- **Chess logic / FEN**: `chess` (python-chess)
- **Engine**: `stockfish` Python wrapper; binary at `/opt/homebrew/bin/stockfish` (falls back to `shutil.which("stockfish")`)
- **Piece-icon assets**: bundled PNGs under `assets/pieces/` are **not used at runtime** in v2 — the old text-display overlay was removed. They remain only as legacy.
- **Calibration image**: `assets/calibration_board.png` — a known starting position used at startup to extract 12 piece templates that match chess.com's exact rendering style. **This file is the only template source; it must ship with the build.**

## Commands

```bash
# Install dependencies
pip install pillow pyautogui opencv-python python-chess stockfish

# Install Stockfish binary
brew install stockfish && stockfish --version

# Run (color may be set on CLI or via the WHITE/BLACK switch later)
python3 main.py --color white
python3 main.py --color black

# Run with per-poll diagnostics:
#   - logs `poll N: rect=... fen=... stable=...`
#   - dumps each cropped board to /tmp/chess_assistant_debug/poll_NNNN.png
#   - logs every per-square confidence rejection from the classifier
python3 main.py --color white --debug

# All tests
python3 -m pytest tests/

# Single test file / single test
python3 -m pytest tests/test_pipeline.py
python3 -m pytest tests/test_pipeline.py::test_classify_after_e4_d5
```

## Architecture

### Module responsibilities

| File | Role |
|---|---|
| `main.py` | Entry point; parses `--color`/`--debug`, wires `ControlPanel` + `Highlights` + `Engine` into a `Controller`. The `Controller` owns the `tk.after()` polling loop, stability cache, and lifecycle. |
| `overlay.py` | Two classes: `ControlPanel` (the 150×140 black box with START/STOP toggle + WHITE/BLACK switch + status dot) and `Highlights` (5 borderless Toplevels: 4 thin red bars forming the source-square outline + 1 translucent red destination fill). |
| `screenshot.py` | Full-screen capture via `pyautogui`; strips alpha (macOS returns RGBA) so downstream code can assume 3-channel RGB. |
| `board_detector.py` | `detect()` returns the board bbox in screenshot/native pixels via HSV mask + connected-components; `square_occupancy()` returns an 8×8 bool grid via per-cell std-dev. |
| `piece_classifier.py` | Templates extracted at startup from `assets/calibration_board.png`. `classify_board(img, player_color)` runs per-cell background normalization + `cv2.matchTemplate(TM_CCOEFF_NORMED)`, gated by a confidence floor (`MIN_SCORE`/`MIN_MARGIN`). Returns `None` on any per-square ambiguity — *fail closed*. |
| `engine.py` | Stockfish wrapper at Skill 20, 500 ms minimum think time. **Auto-respawns the subprocess** on `StockfishException` and retries once (illegal positions can kill the child). |
| `frame_tracker.py` | **Mostly orphaned at runtime in v2.** Only the module-level helpers `sq_to_rc(square, player_color)` and `_set_valid_turn(board, preferred)` are imported by `main.py`. The `FrameTracker` class (Capture-style delta inference, `update()`, `infer_from_image()`) is kept solely to satisfy the existing pipeline tests; the polling loop classifies every frame from scratch. |

### The polling loop (`Controller._poll_once` in `main.py`)

Runs once per `POLL_INTERVAL_MS` (currently 1000 ms) while the toggle is on. Each tick:

1. **Lift the panel** (`panel.lift_panel()`) — macOS demotes `overrideredirect` windows when another app gains focus, so we re-assert `-topmost` every cycle.
2. `screenshot.capture()` — full-screen native pixels (3-channel RGB).
3. `board_detector.detect(arr)` → `(x, y, w, h)` in screenshot pixels. If `None`: hide highlights, reset stability.
4. `board_detector.crop(arr, rect)` → cropped board image.
5. `piece_classifier.classify_board(board_img, player_color, debug_log=...)` → `chess.Board` with pieces placed, or `None` if any cell's match was rejected by the confidence gate.
6. `_set_valid_turn(inferred, player_color)` (from `frame_tracker.py`) — prefer the user's color but flip to the opponent if that's the only legal turn assignment.
7. If `inferred.turn != player_color`: it's the opponent's turn. Hide highlights, reset stability.
8. **Stability filter**: compute `fen = inferred.fen()`. If different from `self._last_fen`, store it, set `_stable_count = 1`, hide highlights, *return without querying Stockfish*. If same: increment `_stable_count`. Only when `_stable_count >= STABILITY_REQUIRED` (currently 2) does the loop proceed.
9. `engine.best_move(fen)` → UCI string.
10. Compute `dpi_scale = arr.shape[1] / panel.root.winfo_screenwidth()` (typically 2.0 on Retina).
11. `highlights.show(rect, move.from_square, move.to_square, player_color, dpi_scale)` — positions the 5 Toplevels in **logical points** (image pixels ÷ dpi_scale).

The next poll is scheduled in `finally`, so even an exception still re-schedules.

### Critical: Retina / DPI conversion

`pyautogui.screenshot()` returns **native pixels** (3024×1964 on this MacBook). `tkinter` geometry uses **logical points** (1512×982). `board_detector.detect()` returns image-pixel coords. **Every overlay positioning calculation must divide by `dpi_scale`**, computed as `screenshot_width / root.winfo_screenwidth()`. Forgetting this places highlights off-board by 2×.

### Stability filter — *why* it exists

chess.com animates piece slides for ~150 ms, shows legal-move dots on hover, and tints last-move squares yellow. A single poll captured mid-animation classifies a half-piece in a cell. Requiring 2 consecutive polls to agree on the FEN (effective ~2 s wait after a move stabilizes) filters all of these transients out. The filter is reset whenever the user toggles off, switches color, or the turn isn't theirs.

### Confidence floor — *why* the classifier fails closed

`piece_classifier._match_type` ranks all 6 same-color templates and rejects (returns `None`) when:
- `best_score < MIN_SCORE` (currently 0.35) — cell doesn't look like any piece, almost certainly corrupted, or
- `best_score - second_score < MIN_MARGIN` (currently 0.05) — top two candidates are too close to call.

Real chess.com pieces score ≥ 0.8 with ≥ 0.28 margin in the test fixtures, so the thresholds reject only genuinely bad cells. `classify_board` propagates a single rejection as `None` for the whole frame — the polling loop hides the overlay instead of showing the wrong piece.

### Always-on-top across applications

On macOS, `wm_attributes("-topmost", True)` only floats above other tkinter windows in the same process. To float above *other apps* (chess.com in Chrome) the code calls:

```python
window.tk.call("::tk::unsupported::MacWindowStyle", "style", window._w, "floating", "noActivates")
```

— see `_mac_float()` in `overlay.py`. Both the control panel and every highlight Toplevel use `noActivates=True`, which still routes clicks to widgets (the toggle / color buttons fire) but never pulls focus from chess.com.

### Square ↔ pixel orientation

`sq_to_rc(square, player_color)` in `frame_tracker.py` is the single source of truth. When playing **White**: row 0 = rank 8, col 0 = file a. When playing **Black**: row 0 = rank 1, col 0 = file h (board is visually flipped). The `Highlights.show()` translation depends on this contract; the test `test_sq_to_rc_matches_white_and_black` locks it in.

### Color switch at runtime

The WHITE/BLACK button on the control panel calls `Controller.on_color_change(is_white)`, which:
1. Updates `self.player_color`.
2. **Resets the stability cache** (`_last_fen = None`, `_stable_count = 0`).
3. Hides any current highlight.

The cache reset is essential: a stale FEN inferred under the old orientation maps to wrong squares under the new one.

### Debug mode (`--debug`)

- Each poll prints `poll N: rect=(x,y,w,h) fen=<...> stable=<count>`.
- Cropped board is saved to `/tmp/chess_assistant_debug/poll_NNNN.png` via `cv2.imwrite` (RGB→BGR).
- Any per-square confidence rejection is logged: `reject e4: low_score best=wP:0.21 second=wB:0.18`.
- Toggle-state and color-change events log: `color switched to BLACK; stability reset`.

This is the right tool for reproducing "wrong piece highlighted" reports — the saved image + the logged FEN + classifier rejections give complete forensics.

## Constraints

- **Player color** defaults to the `--color` CLI arg (or terminal prompt if omitted) and may be flipped at runtime via the WHITE/BLACK switch. Never auto-detected from board orientation.
- **Polling cadence**: `POLL_INTERVAL_MS = 1000` ms, `STABILITY_REQUIRED = 2` consecutive polls. Tuned so a real move stabilizes in ~2 s but animations resolve within one cycle.
- **Stockfish call**: exactly one `get_best_move_time(500)` per stable frame. Parameters: `{"Skill Level": 20, "Threads": 2, "Hash": 128}`.
- **Fail-closed everywhere**: any failure (no board, classifier ambiguity, opponent's turn, malformed UCI, engine crash) hides highlights and resets stability. Better no overlay than a wrong overlay.
- Do **not** call `subprocess` directly for Stockfish — use the `stockfish` Python package.
- All state is in-memory only — nothing written to disk between runs except optional `--debug` image dumps.
- `print()` only for logging — no logging framework.

## Error handling

| Condition | Behavior |
|---|---|
| Board not detected | Hide highlights, reset stability, schedule next poll |
| Screenshot fails | `print` error, hide highlights, schedule next poll |
| Classifier returns `None` (any cell low-confidence) | Hide highlights, reset stability, schedule next poll |
| Inferred position has no legal turn assignment | Hide highlights, reset stability, schedule next poll |
| Opponent's turn | Hide highlights, reset stability, schedule next poll |
| FEN differs between polls (animation, hover, etc.) | Hide highlights, store new FEN, next poll will confirm |
| Stockfish process crashes | `engine.py` respawns + retries once; on second failure returns `None`, overlay hides for that frame |
| Malformed UCI from engine | `print` warning, hide highlights, schedule next poll |
| Stockfish missing at startup | `print` error + `sys.exit(1)` |

## Testing

### Test fixtures in `tests/`

| File | Purpose |
|---|---|
| `tests/debug_screenshot.png` | Full macOS screenshot (3024×1964) — `board_detector.detect()` must locate the board. |
| `tests/board.png` | Cropped starting position — used as `classify_board()` ground truth and as Frame 0 for the legacy delta-inference tests. |
| `tests/board2.png` | Cropped after 1.e4 d5 — Frame 1 for delta inference; ground truth for the classifier post-move. |
| `tests/debug_board.png` | Intermediate visual-inspection fixture. |

### Test files and known test cases

**`tests/test_engine.py`** — may call the real Stockfish binary; skips gracefully if missing.

- `test_starting_position`, `test_mate_in_one`

**`tests/test_pipeline.py`** — runs entirely on disk fixtures; mocks/skips Stockfish where needed.

- Board detection: `test_board_detected_frame0`, `test_board_detected_frame1`.
- Occupancy: `test_starting_position_empty_squares`, `test_frame1_empty_squares`.
- Legacy `FrameTracker` (still tested even though main.py doesn't use it): `test_frame0_sets_starting_fen`, `test_frame1_infers_e4`, `test_infer_from_image_sets_turn`, `test_infer_flips_turn_when_player_choice_illegal`.
- Classifier ground truth: `test_classify_starting_position`, `test_classify_after_e4_d5`.
- v2-specific:
  - `test_sq_to_rc_matches_white_and_black` — locks the orientation contract used by `Highlights`.
  - `test_low_confidence_match_rejected` — verifies the confidence floor on `_match_type` so a regression in `MIN_SCORE` / `MIN_MARGIN` can't silently re-enable wrong-piece highlights.
- `test_engine_responds_after_e4` — end-to-end smoke when Stockfish is present.

### Test conventions

- All pipeline tests load fixtures via `_load(name)` which calls `cv2.imread` then `cv2.cvtColor(BGR → RGB)`. Production code paths assume 3-channel RGB everywhere; never feed BGR or RGBA to detector/classifier.
- The classifier and detector tests do **not** require the polling loop or any tkinter; they run headless in CI.
- When adding tests that involve `chess.Board`, prefer asserting `board.board_fen()` (piece placement only) over full `fen()` to avoid coupling to castling/turn/halfmove fields.
