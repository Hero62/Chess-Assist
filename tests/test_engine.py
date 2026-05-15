import os
import shutil

import chess
import pytest

stockfish_available = (
    os.path.exists("/opt/homebrew/bin/stockfish") or shutil.which("stockfish") is not None
)

pytestmark = pytest.mark.skipif(
    not stockfish_available, reason="Stockfish binary not installed"
)


def test_starting_position():
    from engine import Engine

    eng = Engine()
    move = eng.best_move(chess.STARTING_FEN)
    assert move is not None
    assert chess.Move.from_uci(move) in chess.Board().legal_moves


def test_mate_in_one():
    from engine import Engine

    eng = Engine()
    # Back-rank mate: White rook on a1, Black king on g8, no escape squares.
    fen = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
    move = eng.best_move(fen)
    assert move == "a1a8"
