import os
import shutil
import sys

from stockfish import Stockfish
from stockfish.models import StockfishException

STOCKFISH_PATH = "/opt/homebrew/bin/stockfish"
PARAMETERS = {"Skill Level": 20, "Threads": 2, "Hash": 128}


def _resolve_path() -> str:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = os.path.join(meipass, "bin", "stockfish")
        if os.path.exists(bundled):
            return bundled
    if os.path.exists(STOCKFISH_PATH):
        return STOCKFISH_PATH
    fallback = shutil.which("stockfish")
    if fallback:
        return fallback
    print("Error: Stockfish binary not found. Install with: brew install stockfish")
    sys.exit(1)


class Engine:
    """Stockfish wrapper. Auto-restarts the subprocess if it crashes.

    Illegal or unusual positions can kill the Stockfish process, leaving the
    python-chess Stockfish object holding a dead pipe. Without recovery the
    user has to relaunch the whole app — so on any StockfishException we
    spawn a fresh process and retry once.
    """

    def __init__(self) -> None:
        self._path = _resolve_path()
        self._spawn()

    def _spawn(self) -> None:
        self.sf = Stockfish(path=self._path, parameters=PARAMETERS)

    def best_move(self, fen: str) -> str | None:
        for attempt in (1, 2):
            try:
                self.sf.set_fen_position(fen)
                return self.sf.get_best_move_time(500)
            except StockfishException:
                if attempt == 1:
                    print("Warning: Stockfish process crashed; restarting…")
                    self._spawn()
                else:
                    print("Error: Stockfish crashed again after restart; skipping suggestion")
                    return None
        return None
