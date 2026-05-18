"""Entry point for the v2 chess assistant.

The single toggle button on `ControlPanel` drives an automated polling loop:
every 2.5 seconds while toggled on, the controller takes a screenshot, locates
the chess.com board, classifies every piece, queries Stockfish for the best
move, and renders highlight overlays directly over the board. No buttons
beyond the toggle; no per-move user action.

Stability filter
----------------
Transient screen states (chess.com animating a piece slide, hover dots,
premove arrows) can corrupt one or two cells for ~100–300 ms. To avoid
flashing the wrong highlight, the controller requires `STABILITY_REQUIRED`
consecutive polls to produce the **same FEN** before it queries Stockfish.
Animations and other transients resolve within one cycle, so this adds at
most ~2.5 s of latency after a real move stabilizes.

Debug mode
----------
`--debug` enables per-poll diagnostics:
  * each poll prints `poll N: rect=... fen=... stable=...`
  * any per-square confidence rejection inside `piece_classifier` is logged
  * the cropped board image is saved to /tmp/chess_assistant_debug/poll_N.png

This is the right tool for reproducing "wrong piece highlighted" reports —
the saved image and the logged FEN show exactly what the classifier saw.
"""
from __future__ import annotations

import argparse
import os

import chess
import cv2

import board_detector
import piece_classifier
import screenshot
from engine import Engine
from frame_tracker import _set_valid_turn
from overlay import ControlPanel, Highlights

POLL_INTERVAL_MS = 1000
STABILITY_REQUIRED = 2
DEBUG_DIR = "/tmp/chess_assistant_debug"


def parse_color(value: str) -> chess.Color:
    v = value.strip().lower()
    if v in ("w", "white"):
        return chess.WHITE
    if v in ("b", "black"):
        return chess.BLACK
    raise ValueError(f"Invalid color: {value!r}")


class Controller:
    def __init__(
        self,
        player_color: chess.Color,
        panel: ControlPanel,
        highlights: Highlights,
        engine: Engine,
        debug: bool = False,
    ) -> None:
        self.player_color = player_color
        self.panel = panel
        self.highlights = highlights
        self.engine = engine
        self.debug = debug
        self._after_id: str | None = None
        self._last_fen: str | None = None
        self._stable_count: int = 0
        self._poll_n: int = 0
        if debug:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            print(f"[debug] saving cropped boards to {DEBUG_DIR}")

    def on_toggle(self, state: bool) -> None:
        self._cancel_pending()
        self._reset_stability()
        self.highlights.hide()
        if state:
            self._poll()

    def on_color_change(self, is_white: bool) -> None:
        """Flip the player color at runtime.

        Resets the stability cache and hides any current highlight: the
        cached FEN was inferred under the old orientation, so it would map
        to the wrong board squares if reused. The next poll re-classifies
        with the new color and starts a fresh stability cycle.
        """
        self.player_color = chess.WHITE if is_white else chess.BLACK
        self._reset_stability()
        self.highlights.hide()
        if self.debug:
            side = "WHITE" if is_white else "BLACK"
            print(f"color switched to {side}; stability reset")

    def _cancel_pending(self) -> None:
        if self._after_id is not None:
            try:
                self.panel.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _schedule_next(self) -> None:
        self._after_id = self.panel.root.after(POLL_INTERVAL_MS, self._poll)

    def _poll(self) -> None:
        self._after_id = None
        try:
            self._poll_once()
        finally:
            self._schedule_next()

    def _classifier_log(self, msg: str) -> None:
        # Routes per-square diagnostics from piece_classifier to stdout when
        # --debug is on. No-op otherwise.
        if self.debug:
            print(msg)

    def _poll_once(self) -> None:
        self._poll_n += 1
        # Re-assert always-on-top each cycle — macOS demotes overrideredirect
        # windows when another app gains focus, so passive `-topmost` alone
        # isn't enough.
        self.panel.lift_panel()
        arr = screenshot.capture()
        if arr is None:
            self.highlights.hide()
            return

        rect = board_detector.detect(arr)
        if rect is None:
            if self.debug:
                print(f"poll {self._poll_n}: no board detected")
            self._reset_stability()
            self.highlights.hide()
            return

        board_img = board_detector.crop(arr, rect)
        if self.debug:
            path = os.path.join(DEBUG_DIR, f"poll_{self._poll_n:04d}.png")
            cv2.imwrite(path, cv2.cvtColor(board_img, cv2.COLOR_RGB2BGR))

        inferred = piece_classifier.classify_board(
            board_img, self.player_color, debug_log=self._classifier_log
        )
        if inferred is None:
            if self.debug:
                print(f"poll {self._poll_n}: classifier returned None")
            self._reset_stability()
            self.highlights.hide()
            return

        if not _set_valid_turn(inferred, self.player_color):
            if self.debug:
                print(f"poll {self._poll_n}: no legal turn for inferred position")
            self._reset_stability()
            self.highlights.hide()
            return

        if inferred.turn != self.player_color:
            # Opponent's turn — no suggestion right now. Reset stability so
            # the next user-turn FEN starts a fresh confirmation cycle.
            if self.debug:
                print(f"poll {self._poll_n}: opponent's turn")
            self._reset_stability()
            self.highlights.hide()
            return

        fen = inferred.fen()
        if fen != self._last_fen:
            self._last_fen = fen
            self._stable_count = 1
            if self.debug:
                print(f"poll {self._poll_n}: rect={rect} fen={fen!r} stable=1 (new)")
            # Don't show overlay yet — wait for confirmation poll.
            self.highlights.hide()
            return

        self._stable_count += 1
        if self.debug:
            print(
                f"poll {self._poll_n}: rect={rect} fen={fen!r} "
                f"stable={self._stable_count}"
            )
        if self._stable_count < STABILITY_REQUIRED:
            return  # leave previous highlight alone (likely hidden)

        uci = self.engine.best_move(fen)
        if uci is None:
            self.highlights.hide()
            return

        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            print(f"Warning: engine returned malformed UCI: {uci!r}")
            self.highlights.hide()
            return

        dpi_scale = arr.shape[1] / self.panel.root.winfo_screenwidth()
        self.highlights.show(
            rect, move.from_square, move.to_square, self.player_color, dpi_scale
        )

    def _reset_stability(self) -> None:
        self._last_fen = None
        self._stable_count = 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Chess.com move-suggestion overlay (v2)")
    parser.add_argument("--color", default=None, help="white or black")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log FEN per poll, save cropped boards to /tmp/chess_assistant_debug/",
    )
    args = parser.parse_args()

    color_str = args.color if args.color is not None else input("Playing as [white/black]? ")
    player_color = parse_color(color_str)

    engine = Engine()
    panel = ControlPanel()
    highlights = Highlights(panel.root)
    controller = Controller(
        player_color, panel, highlights, engine, debug=args.debug
    )
    panel.set_initial_color(player_color == chess.WHITE)
    panel.set_toggle_callback(controller.on_toggle)
    panel.set_color_callback(controller.on_color_change)

    side = "White" if player_color == chess.WHITE else "Black"
    print(
        f"Chess assistant (v2) ready as {side}. Toggle START to begin polling "
        f"every {POLL_INTERVAL_MS/1000:.1f}s. {STABILITY_REQUIRED} consecutive "
        "agreeing polls required before showing a suggestion."
    )

    panel.mainloop()


if __name__ == "__main__":
    main()
