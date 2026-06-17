"""Background per-game Lichess Board streams.

The Lichess Board API stream is the only realtime source for a player's own game
(the ``/api/account/playing`` poll is anti-cheat-delayed). It is *designed* to be
held open continuously; opening it per input cost ~2.6 s each time and dominated
input latency. This manager keeps one open stream per active game in a daemon
thread, updates the ``Game`` row as the opponent moves / the game ends, and — when
the device is currently viewing that game — renders and pushes a fresh frame to
LLSS so the move appears automatically without a per-press stream open.

PostgreSQL (MVCC) backs HLSS, so concurrent writes from these threads and the
request handlers are safe; each thread uses its own short-lived session.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

import chess

from hlss.database import SessionLocal
from hlss.models import Game, GameColor, GameStatus, Instance, ScreenType

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    "created": GameStatus.CREATED,
    "started": GameStatus.STARTED,
    "aborted": GameStatus.ABORTED,
    "mate": GameStatus.MATE,
    "resign": GameStatus.RESIGN,
    "stalemate": GameStatus.STALEMATE,
    "timeout": GameStatus.TIMEOUT,
    "draw": GameStatus.DRAW,
    "outoftime": GameStatus.OUT_OF_TIME,
    "cheat": GameStatus.CHEAT,
    "nostart": GameStatus.NO_START,
    "variantend": GameStatus.VARIANT_END,
    "unknownfinish": GameStatus.UNKNOWN_FINISH,
}


def _map_status(name: Optional[str]) -> GameStatus:
    if not name:
        return GameStatus.STARTED
    return _STATUS_MAP.get(str(name).lower(), GameStatus.UNKNOWN_FINISH)


class GameStreamManager:
    """Owns one daemon thread per (instance, game) holding an open Board stream."""

    def __init__(self) -> None:
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def ensure_stream(
        self, instance_id: str, lichess_game_id: str, api_token: str
    ) -> None:
        """Start a stream thread for this game if one isn't already running.
        Safe to call on every input — it no-ops when already streaming."""
        if not (instance_id and lichess_game_id and api_token):
            return
        key = f"{instance_id}:{lichess_game_id}"
        with self._lock:
            existing = self._threads.get(key)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(
                target=self._run,
                args=(key, instance_id, lichess_game_id, api_token),
                name=f"gamestream-{lichess_game_id[:8]}",
                daemon=True,
            )
            self._threads[key] = thread
            thread.start()
            logger.info("game stream started: %s", lichess_game_id)

    def _run(
        self, key: str, instance_id: str, lichess_game_id: str, api_token: str
    ) -> None:
        # Imported here to avoid a circular import at module load.
        from hlss.services.lichess import LichessService

        try:
            lichess = LichessService(api_token)
            initial_fen: Optional[str] = None
            for state in lichess.get_game_stream(lichess_game_id):
                if not isinstance(state, dict):
                    continue
                typ = state.get("type")
                if typ == "gameFull":
                    initial_fen = state.get("initialFen")
                    gamestate = state.get("state") or {}
                elif typ == "gameState":
                    gamestate = state
                else:
                    continue  # chatLine, opponentGone, …
                try:
                    finished = self._apply(instance_id, lichess_game_id, initial_fen, gamestate)
                except Exception:
                    logger.exception("game stream apply failed: %s", lichess_game_id)
                    continue
                if finished:
                    break
        except Exception:
            logger.exception("game stream errored: %s", lichess_game_id)
        finally:
            with self._lock:
                self._threads.pop(key, None)
            logger.info("game stream ended: %s", lichess_game_id)

    def _apply(
        self,
        instance_id: str,
        lichess_game_id: str,
        initial_fen: Optional[str],
        gamestate: dict,
    ) -> bool:
        """Update the Game from a gameState message; push a frame if the device
        is viewing this game. Returns True when the game is finished."""
        moves = gamestate.get("moves") or ""
        status = _map_status(gamestate.get("status"))

        should_push = False
        db = SessionLocal()
        try:
            game = (
                db.query(Game).filter(Game.lichess_game_id == lichess_game_id).first()
            )
            if game is None:
                return True

            start = initial_fen or game.initial_fen or chess.STARTING_FEN
            if start == "startpos":
                start = chess.STARTING_FEN

            board = None
            try:
                board = chess.Board(start)
                for uci in moves.split():
                    board.push(chess.Move.from_uci(uci))
            except Exception:
                board = None  # keep status update even if reconstruction fails

            changed = (moves != (game.moves or "")) or (game.status != status)

            if board is not None:
                game.fen = board.fen()
                pc = chess.WHITE if game.player_color == GameColor.WHITE else chess.BLACK
                game.is_my_turn = (board.turn == pc) and (status == GameStatus.STARTED)
                game.initial_fen = start
            game.moves = moves
            if moves:
                game.last_move = moves.split()[-1]
            game.status = status
            db.commit()

            instance = db.get(Instance, instance_id)
            viewing = (
                instance is not None
                and instance.current_game_id == game.id
                and instance.current_screen == ScreenType.PLAY
            )
            should_push = changed and viewing
            finished = status != GameStatus.STARTED
        finally:
            db.close()

        if should_push:
            self._push_frame(instance_id)
        return finished

    def _push_frame(self, instance_id: str) -> None:
        # Render the current screen and submit it to LLSS so the device picks it
        # up on its next poll. _render_and_submit_frame is async; run it here.
        from hlss.routers.instances import _render_and_submit_frame

        try:
            asyncio.run(_render_and_submit_frame(instance_id))
        except Exception:
            logger.exception("game stream frame push failed: %s", instance_id)


game_stream_manager = GameStreamManager()
