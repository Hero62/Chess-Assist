import numpy as np
import pyautogui


def capture() -> np.ndarray | None:
    try:
        img = pyautogui.screenshot()
        arr = np.array(img)
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        return arr
    except Exception as e:
        print(f"Error: Screenshot failed: {e}")
        return None
