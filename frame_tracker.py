from __future__ import annotations

import chess
import numpy as np

from piece_classifier import classify_board

MAX_INFERENCE_DEPTH = 2


def _set_valid_turn(board: chess.Board, preferred: chess.Color) -> bool:
    for candidate in (preferred, not preferred):
        board.turn = candidate
        if board.is_valid():
            return True
    return False


class FrameTracker:
    """Owns game state, screenshot diff, and last-suggestion cache.

    Occupancy convention: 8x8 bool grid where row 0 is the top of the
    captured board image. When the user plays White, row 0 = rank 8.
    When playing Black, the board is visually flipped so row 0 = rank 1.
    """

    def __init__(self, player_color: chess.Color) -> None:
        self.board = chess.Board()
        self.player_color = player_color
        self.prev_frame: np.ndarray | None = None
        self.frame_count = 0
        self.last_suggestion: tuple[str, str] | None = None

    def is_user_turn(self) -> bool:
        return self.board.turn == self.player_color

    def infer_from_image(self, board_img: np.ndarray, occupancy: np.ndarray) -> bool:
        """Bootstrap state from a single image (the 'Infer' action).

        Visually classifies every piece on `board_img`, replaces the internal
        `chess.Board` with the inferred position, and stores `occupancy` as
        the baseline frame.

        Turn handling: prefer the user's color, but if that produces an
        illegal position (e.g., opponent's king is in check from the user's
        last move — meaning it's actually the opponent's turn now), flip to
        the opponent's color. If neither choice is legal, the position is
        unreachable and we refuse to register it.
        """
        inferred = classify_board(board_img, self.player_color)
        if inferred is None:
            return False
        if not _set_valid_turn(inferred, self.player_color):
            print("Warning: Inferred position is not a legal chess position; ignoring")
            return False
        self.board = inferred
        self.prev_frame = occupancy.copy()
        self.frame_count = 0
        self.last_suggestion = None
        return True

    def update(self, occupancy: np.ndarray) -> tuple[str, object]:
        """Process a new occupancy frame.

        Returns (status, info):
          ('frame0', None)         — first frame; baseline stored.
          ('duplicate', None)      — identical to previous frame.
          ('moved', [chess.Move])  — one or more moves inferred and pushed.
          ('no_move', None)        — delta did not match any legal sequence.
        """
        if self.prev_frame is None:
            self.prev_frame = occupancy.copy()
            self.frame_count = 0
            return ("frame0", None)

        if np.array_equal(occupancy, self.prev_frame):
            print("Duplicate frame — no board change detected, re-showing last suggestion")
            return ("duplicate", None)

        moves = self._infer_moves(occupancy)
        if moves is None:
            print("Warning: Could not infer move from board delta")
            return ("no_move", None)

        for m in moves:
            self.board.push(m)
        self.prev_frame = occupancy.copy()
        self.frame_count += 1
        return ("moved", moves)

    def _sq_to_rc(self, square: int) -> tuple[int, int]:
        file = chess.square_file(square)
        rank = chess.square_rank(square)
        if self.player_color == chess.WHITE:
            return (7 - rank, file)
        return (rank, 7 - file)

    def _occupancy_from_board(self, board: chess.Board) -> np.ndarray:
        occ = np.zeros((8, 8), dtype=bool)
        for sq in chess.SQUARES:
            if board.piece_at(sq) is not None:
                r, c = self._sq_to_rc(sq)
                occ[r, c] = True
        return occ

    def _infer_moves(self, target: np.ndarray) -> list[chess.Move] | None:
        board = self.board.copy()
        return self._search(board, target, MAX_INFERENCE_DEPTH)

    def _search(self, board: chess.Board, target: np.ndarray, depth: int) -> list[chess.Move] | None:
        if depth == 0:
            return None
        for move in board.legal_moves:
            board.push(move)
            if np.array_equal(self._occupancy_from_board(board), target):
                board.pop()
                return [move]
            sub = self._search(board, target, depth - 1)
            board.pop()
            if sub is not None:
                return [move] + sub
        return None
