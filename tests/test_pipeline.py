import os
import shutil

import chess
import cv2
import numpy as np
import pytest

from board_detector import detect, square_occupancy
from frame_tracker import FrameTracker, sq_to_rc
from piece_classifier import classify_board

FIXTURES = os.path.dirname(__file__)


def _load(name: str) -> np.ndarray:
    path = os.path.join(FIXTURES, name)
    img = cv2.imread(path)
    if img is None:
        pytest.skip(f"Missing test fixture: {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def test_board_detected_frame0():
    rect = detect(_load("debug_screenshot.png"))
    assert rect is not None
    x, y, w, h = rect
    assert w > 200 and h > 200


def test_board_detected_frame1():
    # board.png is an already-cropped board; detection should still find a
    # large square region (the board itself bounded by the dark frame).
    rect = detect(_load("board.png"))
    assert rect is not None
    _, _, w, h = rect
    assert w > 100 and h > 100


def test_starting_position_empty_squares():
    occ = square_occupancy(_load("board.png"))
    assert occ[0].all() and occ[1].all(), "ranks 7-8 should be occupied"
    assert occ[6].all() and occ[7].all(), "ranks 1-2 should be occupied"
    assert not occ[2].any() and not occ[3].any(), "ranks 5-6 should be empty"
    assert not occ[4].any() and not occ[5].any(), "ranks 3-4 should be empty"


def test_frame0_sets_starting_fen():
    tracker = FrameTracker(chess.WHITE)
    status, _ = tracker.update(square_occupancy(_load("board.png")))
    assert status == "frame0"
    assert tracker.board.fen() == chess.STARTING_FEN


def test_frame1_empty_squares():
    occ0 = square_occupancy(_load("board.png"))
    occ1 = square_occupancy(_load("board2.png"))
    diff = occ0 != occ1
    # 1.e4 d5: e2/d7 become empty, e4/d5 become occupied → 4 changed squares.
    assert diff.sum() == 4
    # Specifically: e2 (row 6, col 4), e4 (row 4, col 4),
    #               d7 (row 1, col 3), d5 (row 3, col 3)
    assert diff[6, 4] and diff[4, 4] and diff[1, 3] and diff[3, 3]


def test_frame1_infers_e4():
    tracker = FrameTracker(chess.WHITE)
    tracker.update(square_occupancy(_load("board.png")))
    status, moves = tracker.update(square_occupancy(_load("board2.png")))
    assert status == "moved"
    uci = [m.uci() for m in moves]
    assert uci == ["e2e4", "d7d5"]


def test_classify_starting_position():
    img = _load("board.png")
    board = classify_board(img, chess.WHITE)
    assert board is not None
    assert board.board_fen() == chess.Board().board_fen()


def test_classify_after_e4_d5():
    img = _load("board2.png")
    board = classify_board(img, chess.WHITE)
    assert board is not None
    assert board.board_fen() == "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR"


def test_infer_from_image_sets_turn():
    tracker = FrameTracker(chess.WHITE)
    img = _load("board2.png")
    occ = square_occupancy(img)
    assert tracker.infer_from_image(img, occ) is True
    assert tracker.is_user_turn()
    assert tracker.board.piece_at(chess.E4) is not None
    assert tracker.board.piece_at(chess.D5) is not None


def test_infer_flips_turn_when_player_choice_illegal():
    """If forcing turn=player produces an illegal position (e.g., opponent
    is in check), flip to the opponent's turn so Stockfish gets a valid FEN.
    """
    from frame_tracker import _set_valid_turn

    # Black king on e8 is in check from white queen on d7. Setting turn=WHITE
    # is illegal (it implies white moved with black already in check, which
    # is unreachable). Setting turn=BLACK is legal (black must respond).
    board = chess.Board("4k3/3Q4/8/8/8/8/8/4K3 w - - 0 1")
    assert _set_valid_turn(board, chess.WHITE) is True
    assert board.turn == chess.BLACK


def test_engine_responds_after_e4():
    tracker = FrameTracker(chess.WHITE)
    tracker.update(square_occupancy(_load("board.png")))
    tracker.update(square_occupancy(_load("board2.png")))
    assert tracker.board.turn == chess.WHITE
    assert tracker.board.piece_at(chess.E4) is not None
    assert tracker.board.piece_at(chess.D5) is not None

    stockfish_available = (
        os.path.exists("/opt/homebrew/bin/stockfish")
        or shutil.which("stockfish") is not None
    )
    if not stockfish_available:
        pytest.skip("Stockfish binary not installed")

    from engine import Engine

    eng = Engine()
    move = eng.best_move(tracker.board.fen())
    assert move is not None
    assert chess.Move.from_uci(move) in tracker.board.legal_moves


def test_sq_to_rc_matches_white_and_black():
    """Locks the board-image orientation contract that the Highlights
    overlay depends on. From White's POV, e4 (file=4, rank=3) sits at
    image row 4, col 4 (rank 8 is at top). From Black's POV the board is
    flipped, so e4 maps to row 3, col 3.
    """
    # White perspective: row 0 = rank 8, col 0 = file a.
    assert sq_to_rc(chess.E4, chess.WHITE) == (4, 4)
    assert sq_to_rc(chess.A8, chess.WHITE) == (0, 0)
    assert sq_to_rc(chess.H1, chess.WHITE) == (7, 7)

    # Black perspective: row 0 = rank 1, col 0 = file h.
    assert sq_to_rc(chess.E4, chess.BLACK) == (3, 3)
    assert sq_to_rc(chess.A8, chess.BLACK) == (7, 7)
    assert sq_to_rc(chess.H1, chess.BLACK) == (0, 0)


def test_low_confidence_match_rejected():
    """A featureless gray cell does not resemble any chess piece — every
    template's correlation score is near zero. `_match_type` must return
    None instead of arbitrarily picking the (worst) best of a bad lot.

    This is the safety net that prevents the v2 overlay from highlighting
    the wrong piece when a transient screen state (animation, hover dot,
    premove arrow) corrupts a cell.
    """
    from piece_classifier import (
        _match_type,
        _templates,
        TEMPLATE_SIZE,
        NEUTRAL_GRAY,
    )

    templates = _templates()
    assert templates, "calibration templates failed to load — cannot run test"

    # Pure neutral gray "cell" — exactly the post-bg-stripping state, but
    # with no piece silhouette in it. Templates have detailed silhouettes,
    # so correlation should be near zero across the board.
    blank = np.full((TEMPLATE_SIZE, TEMPLATE_SIZE), NEUTRAL_GRAY, dtype=np.uint8)
    assert _match_type(blank, "w", templates) is None
    assert _match_type(blank, "b", templates) is None
