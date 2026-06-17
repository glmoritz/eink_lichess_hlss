"""Local chess engine (Stockfish) wrapper.

Drives a persistent Stockfish process via python-chess so the LocalBackend can
provide an *instant, offline* AI opponent in place of Lichess's AI. One engine
process is reused across moves (guarded by a lock) and is lazily started and
restarted if it dies.

The binary is installed in the image (``stockfish`` in the Dockerfiles); its path
can be overridden with the ``STOCKFISH_PATH`` env var.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import threading
from typing import Optional

import chess
import chess.engine

logger = logging.getLogger(__name__)


def _resolve_stockfish() -> str:
    """Locate the Stockfish binary. ``STOCKFISH_PATH`` wins; otherwise look on
    PATH and the common Debian location ``/usr/games/stockfish`` (the apt package
    installs there, which is NOT on PATH in non-login shells)."""
    env = os.environ.get("STOCKFISH_PATH")
    if env:
        return env
    found = shutil.which("stockfish")
    if found:
        return found
    for candidate in ("/usr/games/stockfish", "/usr/local/bin/stockfish", "/usr/bin/stockfish"):
        if os.path.exists(candidate):
            return candidate
    return "stockfish"  # last resort; will raise clearly if missing


_DEFAULT_PATH = _resolve_stockfish()


class LocalEngineService:
    """Thread-safe wrapper around a persistent Stockfish process."""

    def __init__(self, engine_path: str = _DEFAULT_PATH) -> None:
        self._engine_path = engine_path
        self._engine: Optional[chess.engine.SimpleEngine] = None
        self._lock = threading.Lock()

    # -- engine lifecycle ---------------------------------------------------
    def _ensure_engine(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci(self._engine_path)
        return self._engine

    def _close_locked(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass
            self._engine = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    # -- play ---------------------------------------------------------------
    @staticmethod
    def _skill_and_limit(level: int) -> tuple[int, "chess.engine.Limit"]:
        """Map a 1-8 'AI level' to a Stockfish ``Skill Level`` (0-20) plus a short
        search limit. Low levels play fast and weak — right for a casual device."""
        level = max(1, min(8, int(level)))
        skill = round((level - 1) * 20 / 7)  # 1 -> 0 ... 8 -> 20
        movetime = 0.05 + (level - 1) * 0.05  # 0.05 s .. 0.40 s
        return skill, chess.engine.Limit(time=movetime)

    def best_move(self, board: chess.Board, level: int = 3) -> Optional[chess.Move]:
        """Return Stockfish's move for ``board`` at ``level``, or None if the game
        is already over or the engine could not produce a move."""
        if board.is_game_over():
            return None
        skill, limit = self._skill_and_limit(level)
        with self._lock:
            for attempt in (1, 2):  # one retry: restart a dead engine and try again
                try:
                    engine = self._ensure_engine()
                    engine.configure({"Skill Level": skill})
                    result = engine.play(board, limit)
                    return result.move
                except (
                    chess.engine.EngineError,
                    chess.engine.EngineTerminatedError,
                    BrokenPipeError,
                    OSError,
                ) as exc:
                    logger.warning(
                        "stockfish play failed (attempt %d): %r — restarting", attempt, exc
                    )
                    self._close_locked()
            return None


# Module-level singleton reused across requests/threads.
local_engine = LocalEngineService()

# Ensure the Stockfish subprocess is torn down on interpreter exit (clean app
# shutdown; also lets short-lived scripts exit instead of being kept alive by the
# engine's background thread).
atexit.register(local_engine.close)
