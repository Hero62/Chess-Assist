"""v2 GUI for the chess assistant.

Replaces the old Capture/Infer button overlay with a minimal-UI design:

1. `ControlPanel` — 150×100 black always-on-top window with a toggle button
   and a colored status dot (red = off, green = on).
2. `Highlights` — five borderless Toplevels positioned over the actual
   chess.com board: four thin red bars forming the source-square outline,
   and one translucent white square on the destination.

All highlight Toplevels live on the same tk root as the control panel, so a
single `mainloop()` drives the whole UI.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import chess

from frame_tracker import sq_to_rc


def _mac_float(window: tk.Wm, *, no_activates: bool) -> None:
    """Promote a tk window to the macOS 'floating' window level.

    macOS divides windows into "levels". Plain `wm_attributes("-topmost",
    True)` only floats above other windows in the same process. To float
    above other applications (e.g. chess.com in Chrome) we have to ask
    Carbon for the floating level explicitly. When `no_activates` is True,
    clicking the window does not bring our app to the foreground — critical
    for the highlight overlays so the chess.com tab keeps keyboard focus.
    Silently no-ops on non-macOS Tk builds.
    """
    try:
        style = ("floating", "noActivates") if no_activates else ("floating",)
        window.tk.call(
            "::tk::unsupported::MacWindowStyle", "style", window._w, *style
        )
    except tk.TclError:
        pass

# Control panel
PANEL_W = 150
PANEL_H = 140
DOT_OFF = "#ff3030"
DOT_ON = "#30c060"
COLOR_WHITE_BG = "#f0f0f0"
COLOR_WHITE_FG = "#000000"
COLOR_BLACK_BG = "#222222"
COLOR_BLACK_FG = "#ffffff"

# Highlight overlays
BORDER_THICKNESS = 4         # logical px for the red outline bars
BORDER_COLOR = "#ff2020"
TARGET_COLOR = "#ff2020"     # destination fill — same red as the source outline
TARGET_ALPHA = 0.4           # 60 % transparent per spec


class ControlPanel:
    """Always-on-top 150×100 toggle window."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        # Float above other apps AND don't steal focus when clicked: macOS's
        # "noActivates" still routes the click to the widget (so the toggle
        # button's command fires) but never pulls focus away from chess.com.
        _mac_float(self.root, no_activates=True)
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{PANEL_W}x{PANEL_H}+{screen_w - PANEL_W - 10}+60")
        self.root.configure(bg="black")

        # Top half — START/STOP toggle with status dot
        self.dot_canvas = tk.Canvas(
            self.root, width=40, height=40, bg="black", highlightthickness=0
        )
        self.dot_canvas.place(x=12, y=15)
        self._dot = self.dot_canvas.create_oval(4, 4, 36, 36, fill=DOT_OFF, outline="")

        self.button = tk.Button(
            self.root,
            text="START",
            font=("Arial", 12, "bold"),
            command=self._on_click,
            relief=tk.FLAT,
            bg="#222222",
            fg="white",
            activebackground="#444444",
            activeforeground="white",
            width=7,
        )
        self.button.place(x=62, y=20, width=78, height=30)

        # Divider
        tk.Frame(self.root, bg="#444444", height=1).place(x=10, y=65, width=130, height=1)

        # Bottom half — player-color switch
        self.color_button = tk.Button(
            self.root,
            text="WHITE",
            font=("Arial", 12, "bold"),
            command=self._on_color_click,
            relief=tk.FLAT,
            bg=COLOR_WHITE_BG,
            fg=COLOR_WHITE_FG,
            activebackground="#dddddd",
            activeforeground=COLOR_WHITE_FG,
        )
        self.color_button.place(x=15, y=80, width=120, height=42)

        self._state = False
        self._is_white = True
        self._cb: Callable[[bool], None] | None = None
        self._color_cb: Callable[[bool], None] | None = None

    def set_toggle_callback(self, fn: Callable[[bool], None]) -> None:
        self._cb = fn

    def set_color_callback(self, fn: Callable[[bool], None]) -> None:
        """Register a callback fired when the color switch changes.

        The callback receives `True` for white, `False` for black.
        """
        self._color_cb = fn

    def set_initial_color(self, is_white: bool) -> None:
        """Sync the color button to the `--color` argument at startup.

        Does NOT fire the callback — the controller's initial player_color
        is already correct.
        """
        self._is_white = is_white
        self._render_color_button()

    def _on_click(self) -> None:
        self._state = not self._state
        self.dot_canvas.itemconfig(self._dot, fill=DOT_ON if self._state else DOT_OFF)
        self.button.config(text="STOP" if self._state else "START")
        if self._cb is not None:
            self._cb(self._state)

    def _on_color_click(self) -> None:
        self._is_white = not self._is_white
        self._render_color_button()
        if self._color_cb is not None:
            self._color_cb(self._is_white)

    def _render_color_button(self) -> None:
        if self._is_white:
            self.color_button.config(
                text="WHITE",
                bg=COLOR_WHITE_BG, fg=COLOR_WHITE_FG,
                activebackground="#dddddd", activeforeground=COLOR_WHITE_FG,
            )
        else:
            self.color_button.config(
                text="BLACK",
                bg=COLOR_BLACK_BG, fg=COLOR_BLACK_FG,
                activebackground="#444444", activeforeground=COLOR_BLACK_FG,
            )

    def lift_panel(self) -> None:
        """Re-assert always-on-top. Call once per poll; macOS sometimes
        demotes overrideredirect windows when another app gains focus."""
        try:
            self.root.wm_attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass

    def mainloop(self) -> None:
        self.root.mainloop()


class Highlights:
    """Five borderless Toplevels: 4-bar source outline + 1 target fill."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root

        def _make(bg: str, alpha: float) -> tk.Toplevel:
            w = tk.Toplevel(root)
            w.overrideredirect(True)
            w.wm_attributes("-topmost", True)
            w.wm_attributes("-alpha", alpha)
            w.configure(bg=bg)
            # Float above other apps; do not steal focus from chess.com when
            # the overlay is positioned or clicked through.
            _mac_float(w, no_activates=True)
            w.withdraw()
            return w

        # Four red bars: top, bottom, left, right of the source square.
        self._border = [_make(BORDER_COLOR, 1.0) for _ in range(4)]
        # Translucent white fill on the destination.
        self._target = _make(TARGET_COLOR, TARGET_ALPHA)

    def show(
        self,
        board_rect_px: tuple[int, int, int, int],
        from_sq: int,
        to_sq: int,
        player_color: chess.Color,
        dpi_scale: float,
    ) -> None:
        """Position and reveal the 5 overlays.

        `board_rect_px` is `(x, y, w, h)` in screenshot/native pixels (what
        `board_detector.detect()` returns). `dpi_scale` converts native px to
        tkinter logical points (typically 2.0 on macOS Retina).
        """
        bx, by, bw, bh = board_rect_px
        board_x = bx / dpi_scale
        board_y = by / dpi_scale
        sq_w = (bw / 8) / dpi_scale
        sq_h = (bh / 8) / dpi_scale

        def square_origin(square: int) -> tuple[float, float]:
            r, c = sq_to_rc(square, player_color)
            return (board_x + c * sq_w, board_y + r * sq_h)

        src_x, src_y = square_origin(from_sq)
        t = BORDER_THICKNESS
        edges = [
            # top, bottom, left, right
            (src_x, src_y, sq_w, t),
            (src_x, src_y + sq_h - t, sq_w, t),
            (src_x, src_y, t, sq_h),
            (src_x + sq_w - t, src_y, t, sq_h),
        ]
        for win, (x, y, w, h) in zip(self._border, edges):
            win.geometry(
                f"{int(round(w))}x{int(round(h))}+{int(round(x))}+{int(round(y))}"
            )
            win.deiconify()
            win.wm_attributes("-topmost", True)
            win.lift()

        dst_x, dst_y = square_origin(to_sq)
        self._target.geometry(
            f"{int(round(sq_w))}x{int(round(sq_h))}"
            f"+{int(round(dst_x))}+{int(round(dst_y))}"
        )
        self._target.deiconify()
        self._target.wm_attributes("-topmost", True)
        self._target.lift()

    def hide(self) -> None:
        for win in self._border:
            win.withdraw()
        self._target.withdraw()
