"""Visual piece classification for bootstrapping an in-progress game.

Used by the 'Infer' action. Normal Frame 1+ updates still use delta-only
inference; piece classification is only run when the user explicitly asks
to register a game already in progress.

Strategy:
- Templates are extracted at startup from a known starting-position image
  (`CALIBRATION_IMAGE`), guaranteeing they match the actual chess.com
  rendering style exactly. The starting position contains all 12 piece
  types and both square shades, so a single image suffices.
- For each occupied square: detect piece color (white/black) by extreme
  pixel count, then template-match against the 6 same-color templates
  using normalized cross-correlation on background-normalized grayscale.
"""
from __future__ import annotations

import os
import sys

import chess
import cv2
import numpy as np

from board_detector import square_occupancy


def _resource(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


CALIBRATION_IMAGE = _resource("assets/calibration_board.png")
TEMPLATE_SIZE = 96
NEUTRAL_GRAY = 128
BG_RGB_DEVIATION = 28

# Confidence floor for accepting a per-square template match. cv2's
# TM_CCOEFF_NORMED returns values in [-1, 1]; a real chess.com piece against
# the calibration templates scores well above 0.5 in practice. Anything
# lower is a corrupted cell (animation mid-flight, hover indicator,
# premove arrow) and must be rejected to avoid showing the wrong piece.
MIN_SCORE = 0.35
MIN_MARGIN = 0.05  # winning template must beat #2 by at least this much

_PIECE_TYPE = {
    "P": chess.PAWN,
    "N": chess.KNIGHT,
    "B": chess.BISHOP,
    "R": chess.ROOK,
    "Q": chess.QUEEN,
    "K": chess.KING,
}

# Calibration source layout (rank 8 = row 0). One sample square per piece type
# per color, taken from the standard starting position.
_CALIBRATION_SAMPLES = {
    "bR": (0, 0), "bN": (0, 1), "bB": (0, 2), "bQ": (0, 3), "bK": (0, 4),
    "bP": (1, 0),
    "wR": (7, 0), "wN": (7, 1), "wB": (7, 2), "wQ": (7, 3), "wK": (7, 4),
    "wP": (6, 0),
}


def _normalize_cell(cell_rgb: np.ndarray) -> np.ndarray:
    """Replace square-background pixels with neutral gray; return TEMPLATE_SIZE grayscale."""
    h, w = cell_rgb.shape[:2]
    pad_y, pad_x = max(2, h // 10), max(2, w // 10)
    corner_pixels = np.concatenate([
        cell_rgb[:pad_y, :pad_x].reshape(-1, 3),
        cell_rgb[:pad_y, -pad_x:].reshape(-1, 3),
        cell_rgb[-pad_y:, :pad_x].reshape(-1, 3),
        cell_rgb[-pad_y:, -pad_x:].reshape(-1, 3),
    ])
    bg_color = np.median(corner_pixels, axis=0)
    diff = np.abs(cell_rgb.astype(np.int16) - bg_color.astype(np.int16))
    is_bg = np.all(diff <= BG_RGB_DEVIATION, axis=2)

    gray = cv2.cvtColor(cell_rgb, cv2.COLOR_RGB2GRAY).copy()
    gray[is_bg] = NEUTRAL_GRAY

    side = max(h, w)
    padded = np.full((side, side), NEUTRAL_GRAY, dtype=np.uint8)
    y_off, x_off = (side - h) // 2, (side - w) // 2
    padded[y_off:y_off + h, x_off:x_off + w] = gray
    return cv2.resize(padded, (TEMPLATE_SIZE, TEMPLATE_SIZE), interpolation=cv2.INTER_AREA)


def _extract_cell(board_img: np.ndarray, row: int, col: int) -> np.ndarray:
    h, w = board_img.shape[:2]
    cell_h, cell_w = h / 8.0, w / 8.0
    y0, y1 = int(row * cell_h), int((row + 1) * cell_h)
    x0, x1 = int(col * cell_w), int((col + 1) * cell_w)
    return board_img[y0:y1, x0:x1]


def _build_templates() -> dict[str, np.ndarray]:
    if not os.path.exists(CALIBRATION_IMAGE):
        return {}
    bgr = cv2.imread(CALIBRATION_IMAGE)
    if bgr is None:
        return {}
    img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    out: dict[str, np.ndarray] = {}
    for code, (r, c) in _CALIBRATION_SAMPLES.items():
        cell = _extract_cell(img, r, c)
        out[code] = _normalize_cell(cell)
    return out


_TEMPLATE_CACHE: dict[str, np.ndarray] | None = None


def _templates() -> dict[str, np.ndarray]:
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is None:
        _TEMPLATE_CACHE = _build_templates()
    return _TEMPLATE_CACHE


def _piece_color(cell_gray: np.ndarray) -> str:
    """White vs black piece by extreme-pixel count.

    Chess.com square bg is mid-tone. White piece bodies are near 255; black
    piece bodies are near 30. Counting extreme pixels unambiguously
    identifies color regardless of square shade.
    """
    very_dark = int((cell_gray < 70).sum())
    very_bright = int((cell_gray > 245).sum())
    return "b" if very_dark > very_bright else "w"


def _rank_templates(
    cell_norm: np.ndarray, color: str, templates: dict[str, np.ndarray]
) -> list[tuple[str, float]]:
    """Return [(code, score), ...] for all same-color templates, sorted desc."""
    scores: list[tuple[str, float]] = []
    for code, tmpl in templates.items():
        if not code.startswith(color):
            continue
        score = float(cv2.matchTemplate(cell_norm, tmpl, cv2.TM_CCOEFF_NORMED)[0, 0])
        scores.append((code, score))
    scores.sort(key=lambda kv: -kv[1])
    return scores


def _match_type(
    cell_norm: np.ndarray,
    color: str,
    templates: dict[str, np.ndarray],
    *,
    debug_log=None,
    square_name: str | None = None,
) -> str | None:
    """Return the best-matching piece code, or None if confidence is too low.

    A match is rejected when either (a) the top score falls below
    `MIN_SCORE` (cell is too noisy to look like any template — likely a
    transient state) or (b) the top two candidates differ by less than
    `MIN_MARGIN` (the classifier is guessing between two similar shapes).
    """
    ranked = _rank_templates(cell_norm, color, templates)
    if not ranked:
        return None
    best_code, best_score = ranked[0]
    second_code, second_score = ranked[1] if len(ranked) > 1 else ("—", -2.0)
    if best_score < MIN_SCORE or (best_score - second_score) < MIN_MARGIN:
        if debug_log is not None:
            sq = square_name or "?"
            reason = "low_score" if best_score < MIN_SCORE else "tight_margin"
            debug_log(
                f"  reject {sq}: {reason} best={best_code}:{best_score:.3f} "
                f"second={second_code}:{second_score:.3f}"
            )
        return None
    return best_code


def classify_board(
    board_img: np.ndarray,
    player_color: chess.Color,
    *,
    debug_log=None,
) -> chess.Board | None:
    """Visually classify every piece on a cropped board image.

    Returns a `chess.Board` with pieces placed (no move history). Whose turn
    it is must be set by the caller — typically to `player_color` because
    the user only invokes Infer when it's their turn.

    If `debug_log` is supplied, it receives a single-line string for any
    per-square rejection. A single rejected square aborts the whole
    classification (returns None) so the caller fails closed rather than
    surfacing a partial / wrong position.
    """
    if board_img is None or board_img.ndim != 3:
        return None
    templates = _templates()
    if not templates:
        return None

    gray = cv2.cvtColor(board_img, cv2.COLOR_RGB2GRAY)
    occ = square_occupancy(board_img)

    board = chess.Board.empty()
    for r in range(8):
        for c in range(8):
            if not occ[r, c]:
                continue
            if player_color == chess.WHITE:
                rank, file = 7 - r, c
            else:
                rank, file = r, 7 - c
            sq = chess.square(file, rank)
            sq_name = chess.square_name(sq)

            cell_rgb = _extract_cell(board_img, r, c)
            cell_gray = _extract_cell(gray, r, c)
            if cell_gray.size == 0:
                continue
            color = _piece_color(cell_gray)
            normalized = _normalize_cell(cell_rgb)
            code = _match_type(
                normalized, color, templates,
                debug_log=debug_log, square_name=sq_name,
            )
            if code is None:
                if debug_log is not None:
                    debug_log(f"classify_board: aborted at {sq_name}")
                return None

            piece = chess.Piece(
                _PIECE_TYPE[code[1]],
                chess.WHITE if color == "w" else chess.BLACK,
            )
            board.set_piece_at(sq, piece)

    return board
