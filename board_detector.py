import cv2
import numpy as np

OCCUPANCY_STD_THRESHOLD = 18.0


CHESSCOM_GREEN_LOW = (30, 30, 60)
CHESSCOM_GREEN_HIGH = (80, 180, 200)

_MORPH_KERNEL = np.ones((15, 15), np.uint8)


def detect(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """Locate the chess board in a screenshot. Returns (x, y, w, h) or None.

    Uses HSV color masking on chess.com's signature dark-square green: mask
    the dark squares, close the gaps so the 32 squares merge into one blob,
    take the largest square-ish connected component.
    """
    if image is None:
        return None
    if image.ndim != 3:
        return None

    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, CHESSCOM_GREEN_LOW, CHESSCOM_GREEN_HIGH)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _MORPH_KERNEL)

    num, _, stats, _ = cv2.connectedComponentsWithStats(closed)
    if num <= 1:
        return None

    img_h, img_w = image.shape[:2]
    min_side = min(img_h, img_w) * 0.1

    best = None
    best_area = 0
    for i in range(1, num):
        x, y, w, h, area = stats[i]
        if w < min_side or h < min_side:
            continue
        aspect = w / h if h > 0 else 0
        if not (0.85 <= aspect <= 1.15):
            continue
        if area > best_area:
            best_area = area
            best = (int(x), int(y), int(w), int(h))
    return best


def crop(image: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    return image[y:y + h, x:x + w]


def square_occupancy(board_img: np.ndarray, threshold: float = OCCUPANCY_STD_THRESHOLD) -> np.ndarray:
    """Return 8x8 bool occupancy grid. Row 0 = top of image."""
    if board_img.ndim == 3:
        gray = cv2.cvtColor(board_img, cv2.COLOR_RGB2GRAY)
    else:
        gray = board_img

    h, w = gray.shape[:2]
    cell_h, cell_w = h / 8.0, w / 8.0
    y0s = (np.arange(8) * cell_h + cell_h * 0.22).astype(int)
    y1s = (np.arange(8) * cell_h + cell_h * 0.78).astype(int)
    x0s = (np.arange(8) * cell_w + cell_w * 0.22).astype(int)
    x1s = (np.arange(8) * cell_w + cell_w * 0.78).astype(int)
    occ = np.zeros((8, 8), dtype=bool)

    for r in range(8):
        for c in range(8):
            cell = gray[y0s[r]:y1s[r], x0s[c]:x1s[c]]
            if cell.size == 0:
                continue
            occ[r, c] = float(cell.std()) > threshold
    return occ
