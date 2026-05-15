from __future__ import annotations

import argparse
import sys

import chess

from board_detector import crop, detect, square_occupancy
from engine import Engine
from frame_tracker import FrameTracker
from overlay import Overlay
from screenshot import capture


def parse_color(value: str) -> chess.Color:
    v = value.strip().lower()
    if v in ("w", "white"):
        return chess.WHITE
    if v in ("b", "black"):
        return chess.BLACK
    raise ValueError(f"Invalid color: {value!r}")


def move_display_info(board: chess.Board, move: chess.Move) -> tuple[str | None, str]:
    """Return (piece_code, label) for displaying a suggested move."""
    piece = board.piece_at(move.from_square)
    dest = chess.square_name(move.to_square)
    if piece is None:
        return None, dest
    color = "w" if piece.color == chess.WHITE else "b"
    piece_code = f"{color}{piece.symbol().upper()}"
    label = dest if piece.piece_type == chess.PAWN else f"{piece.symbol().upper()}{dest}"
    return piece_code, label


class Controller:
    def __init__(self, player_color: chess.Color, overlay: Overlay) -> None:
        self.player_color = player_color
        self.engine = Engine()
        self.tracker = FrameTracker(player_color)
        self.overlay = overlay
        self.fail_count = 0

    def _grab_board(self) -> tuple[bool, "any"]:
        """Screenshot + detect + crop. Returns (ok, board_img | None)."""
        img = capture()
        if img is None:
            return (False, None)
        rect = detect(img)
        if rect is None:
            self.fail_count += 1
            self.overlay.show_no_board(self.fail_count)
            return (False, None)
        self.fail_count = 0
        self.overlay.clear_fail()
        return (True, crop(img, rect))

    def _suggest(self) -> None:
        """Compute and display the best move for the current position."""
        if not self.tracker.is_user_turn():
            self.overlay.show_waiting()
            return
        move_uci = self.engine.best_move(self.tracker.board.fen())
        if move_uci is None:
            self.overlay.show_game_over()
            return
        move = chess.Move.from_uci(move_uci)
        piece_code, label = move_display_info(self.tracker.board, move)
        if piece_code is None:
            self.overlay.show_unknown()
            return
        self.tracker.last_suggestion = (piece_code, label)
        self.overlay.show_move(piece_code, label)

    def on_capture(self) -> None:
        ok, board_img = self._grab_board()
        if not ok:
            return
        occ = square_occupancy(board_img)
        status, _ = self.tracker.update(occ)
        if status == "duplicate":
            return
        if status == "no_move":
            self.overlay.show_unknown()
            return
        self._suggest()

    def on_infer(self) -> None:
        ok, board_img = self._grab_board()
        if not ok:
            return
        occ = square_occupancy(board_img)
        if not self.tracker.infer_from_image(board_img, occ):
            print("Warning: Could not infer position from image")
            self.overlay.show_unknown()
            return
        print(f"Inferred position: {self.tracker.board.fen()}")
        self._suggest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chess.com move-suggestion overlay")
    parser.add_argument("--color", default=None, help="white or black")
    args = parser.parse_args()

    if args.color is not None:
        player_color = parse_color(args.color)
    else:
        player_color = None
        while player_color is None:
            try:
                color_str = input("Playing as [white/black]? ").strip()
            except EOFError:
                print("Error: --color argument required when stdin is not a terminal.")
                sys.exit(1)
            try:
                player_color = parse_color(color_str)
            except ValueError:
                print("Please enter 'white' or 'black'.")

    overlay = Overlay()
    controller = Controller(player_color, overlay)
    overlay.set_capture_callback(controller.on_capture)
    overlay.set_infer_callback(controller.on_infer)

    side = "White" if player_color == chess.WHITE else "Black"
    print(f"Chess assistant running as {side}. Click Capture after each move, or Infer to register an in-progress game.")

    overlay.mainloop()


if __name__ == "__main__":
    main()
