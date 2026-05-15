from __future__ import annotations

import os
import sys
import tkinter as tk
from typing import Callable

from PIL import Image, ImageTk


def _resource(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


PIECE_DIR = _resource("assets/pieces")
WINDOW_W = 140
WINDOW_H = 220


class Overlay:
    """Always-on-top tkinter window with move display + Capture/Infer buttons.

    The user clicks Capture to score the position after a move was made on
    the board. Infer registers an in-progress game by visually classifying
    every piece (used once, when starting from a non-initial position).
    """

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+{screen_w - WINDOW_W - 10}+100")
        self.root.configure(bg="black")

        self.fail_label = tk.Label(
            self.root, text="", font=("Arial", 11), fg="white", bg="black"
        )
        self.fail_label.pack(side=tk.TOP, pady=(2, 0))

        self.body = tk.Frame(self.root, bg="black", height=100)
        self.body.pack(side=tk.TOP, fill=tk.X, expand=False)
        self.body.pack_propagate(False)

        self.icon_label = tk.Label(self.body, bg="black")
        self.icon_label.pack(side=tk.LEFT, padx=(8, 2), pady=4)

        self.text_label = tk.Label(
            self.body, text="...", font=("Arial", 18, "bold"), fg="white", bg="black"
        )
        self.text_label.pack(side=tk.LEFT)

        self.button_frame = tk.Frame(self.root, bg="black")
        self.button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=4)

        self.capture_btn = tk.Button(
            self.button_frame,
            text="Capture",
            font=("Arial", 11, "bold"),
            command=self._on_capture,
            relief=tk.FLAT,
            bg="#2d6a3e",
            fg="white",
            activebackground="#3a8a52",
            activeforeground="white",
        )
        self.capture_btn.pack(fill=tk.X, padx=8, pady=(0, 4))

        self.infer_btn = tk.Button(
            self.button_frame,
            text="Infer",
            font=("Arial", 11, "bold"),
            command=self._on_infer,
            relief=tk.FLAT,
            bg="#3a3a8a",
            fg="white",
            activebackground="#4a4ab8",
            activeforeground="white",
        )
        self.infer_btn.pack(fill=tk.X, padx=8)

        self._icon_cache: dict[str, ImageTk.PhotoImage] = {}
        self._current_icon: ImageTk.PhotoImage | None = None
        self._capture_cb: Callable[[], None] | None = None
        self._infer_cb: Callable[[], None] | None = None

    def set_capture_callback(self, fn: Callable[[], None]) -> None:
        self._capture_cb = fn

    def set_infer_callback(self, fn: Callable[[], None]) -> None:
        self._infer_cb = fn

    def _on_capture(self) -> None:
        if self._capture_cb is not None:
            self._capture_cb()

    def _on_infer(self) -> None:
        if self._infer_cb is not None:
            self._infer_cb()

    def _load_icon(self, piece_code: str) -> ImageTk.PhotoImage | None:
        if piece_code in self._icon_cache:
            return self._icon_cache[piece_code]
        path = os.path.join(PIECE_DIR, f"{piece_code}.png")
        if not os.path.exists(path):
            return None
        img = Image.open(path).resize((56, 56), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self._icon_cache[piece_code] = photo
        return photo

    def show_move(self, piece_code: str, square_label: str) -> None:
        icon = self._load_icon(piece_code)
        if icon is not None:
            self.icon_label.config(image=icon, text="")
            self._current_icon = icon
        else:
            self.icon_label.config(image="", text=piece_code)
            self._current_icon = None
        self.text_label.config(text=square_label)

    def show_waiting(self) -> None:
        self.icon_label.config(image="", text="")
        self.text_label.config(text="...")
        self._current_icon = None

    def show_unknown(self) -> None:
        self.icon_label.config(image="", text="")
        self.text_label.config(text="?")
        self._current_icon = None

    def show_game_over(self) -> None:
        self.icon_label.config(image="", text="")
        self.text_label.config(text="Game over")
        self._current_icon = None

    def show_no_board(self, fail_count: int) -> None:
        self.fail_label.config(text=f"({fail_count})")

    def clear_fail(self) -> None:
        self.fail_label.config(text="")

    def mainloop(self) -> None:
        self.root.mainloop()
