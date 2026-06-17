"""Pluggable chess backends.

A backend drives game creation and move application for a `Game`, selected by the
`Game.backend` discriminator:

- **local**  — python-chess rules/state + Stockfish AI, our DB as the source of
  truth. Instant, offline; no Lichess.
- **lichess** — the existing Lichess path (make_move + stream/sync), left in place
  in `input_processor` / `instances`.

Milestone-1 Part A wires AI games through `LocalBackend` (instant, works with no
Lichess account). Part B extends `LocalBackend.submit_move` to mirror a move to the
partner instance for local human-vs-human.
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import Optional

import chess
from sqlalchemy.orm import Session

from hlss.models import Game, GameColor, GameStatus, Instance, LichessAccount, ScreenType
from hlss.services.local_engine import local_engine

logger = logging.getLogger(__name__)

BACKEND_LICHESS = "lichess"
BACKEND_LOCAL = "local"

_AI_PREFIX = "Stockfish level "


def _outcome_to_status(outcome) -> GameStatus:
    """python-chess Outcome -> GameStatus (claim_draw=False -> only forced ends)."""
    if outcome.termination == chess.Termination.CHECKMATE:
        return GameStatus.MATE
    if outcome.termination == chess.Termination.STALEMATE:
        return GameStatus.STALEMATE
    # insufficient material / 5-fold repetition / 75-move
    return GameStatus.DRAW


class LocalBackend:
    """Local-engine backend: instant Stockfish AI (Part A) + local human relay (Part B)."""

    # -- board helpers ------------------------------------------------------
    def _board(self, game: Game) -> chess.Board:
        start = (
            game.initial_fen
            if (game.initial_fen and game.initial_fen != "startpos")
            else chess.STARTING_FEN
        )
        board = chess.Board(start)
        for uci in (game.moves or "").split():
            board.push(chess.Move.from_uci(uci))
        return board

    def _apply(self, game: Game, board: chess.Board, move: chess.Move) -> None:
        """Push `move` onto `board` and update the Game row + status."""
        board.push(move)
        game.fen = board.fen()
        game.moves = ((game.moves or "") + " " + move.uci()).strip()
        game.last_move = move.uci()
        pc = chess.WHITE if game.player_color == GameColor.WHITE else chess.BLACK
        game.is_my_turn = board.turn == pc
        outcome = board.outcome(claim_draw=False)
        if outcome is not None:
            game.status = _outcome_to_status(outcome)

    @staticmethod
    def _ai_level(game: Game) -> int:
        name = game.opponent_username or ""
        if name.startswith(_AI_PREFIX):
            try:
                return int(name[len(_AI_PREFIX):].strip())
            except ValueError:
                pass
        return 3

    # -- public api ---------------------------------------------------------
    def create_ai_game(
        self,
        db: Session,
        account: LichessAccount,
        instance: Instance,
        level: int,
        color: str,
    ) -> Game:
        """Create a local game vs Stockfish and return it. If the AI is white it
        makes the first move immediately."""
        if color not in ("white", "black"):
            color = random.choice(("white", "black"))
        game = Game(
            lichess_game_id=f"local-{uuid.uuid4()}",
            account_id=account.id,
            backend=BACKEND_LOCAL,
            player_color=GameColor.WHITE if color == "white" else GameColor.BLACK,
            opponent_username=f"{_AI_PREFIX}{level}",
            status=GameStatus.STARTED,
            fen=chess.STARTING_FEN,
            initial_fen=chess.STARTING_FEN,
            moves="",
            is_my_turn=(color == "white"),
            instance_id=instance.id,
        )
        db.add(game)
        db.flush()
        if not game.is_my_turn:  # AI plays white -> moves first
            board = chess.Board()
            ai_move = local_engine.best_move(board, level)
            if ai_move is not None:
                self._apply(game, board, ai_move)
        db.commit()
        return game

    def submit_move(self, db: Session, game: Game, uci: str) -> None:
        """Apply the player's move; then either append Stockfish's reply (AI
        game) or mirror the move into the partner's row (local human match).
        Synchronous, no network."""
        push_instance: Optional[str] = None
        try:
            board = self._board(game)
            mv = chess.Move.from_uci(uci)
            if mv not in board.legal_moves:
                return
            self._apply(game, board, mv)
            game.move_state = None
            if game.match_id is not None:
                # Local human match: copy the move into the partner's row.
                push_instance = self._mirror_to_partner(db, game, board)
            elif game.status == GameStatus.STARTED and not game.is_my_turn:
                # AI game: append Stockfish's reply.
                ai_move = local_engine.best_move(board, self._ai_level(game))
                if ai_move is not None:
                    self._apply(game, board, ai_move)
            db.commit()
        except Exception:
            db.rollback()
            raise
        # Push the partner's frame AFTER commit so it renders the new state.
        if push_instance:
            self._push_frame(push_instance)

    # -- local human-vs-human ----------------------------------------------
    def create_human_match(
        self,
        db: Session,
        initiator_account: LichessAccount,
        initiator_instance: Instance,
        partner_account: LichessAccount,
        partner_instance: Instance,
        color: str,
    ) -> Game:
        """Create a local human match as two mirrored Game rows (one per
        instance) sharing a match_id, opened on both instances. Returns the
        initiator's row."""
        if color not in ("white", "black"):
            color = random.choice(("white", "black"))
        partner_color = "black" if color == "white" else "white"
        match_id = str(uuid.uuid4())

        def _mk(account: LichessAccount, instance: Instance, side: str, opp: str) -> Game:
            return Game(
                lichess_game_id=f"local-{uuid.uuid4()}",
                account_id=account.id,
                backend=BACKEND_LOCAL,
                match_id=match_id,
                instance_id=instance.id,
                player_color=GameColor.WHITE if side == "white" else GameColor.BLACK,
                opponent_username=opp,
                status=GameStatus.STARTED,
                fen=chess.STARTING_FEN,
                initial_fen=chess.STARTING_FEN,
                moves="",
                is_my_turn=(side == "white"),
            )

        g_init = _mk(initiator_account, initiator_instance, color, partner_account.username)
        g_partner = _mk(
            partner_account, partner_instance, partner_color, initiator_account.username
        )
        db.add(g_init)
        db.add(g_partner)
        db.flush()

        # Open the game on the partner's instance too, then show it.
        partner_instance.current_screen = ScreenType.PLAY
        partner_instance.current_game_id = g_partner.id
        db.commit()
        self._push_frame(partner_instance.id)
        return g_init

    def _mirror_to_partner(
        self, db: Session, game: Game, board: chess.Board
    ) -> Optional[str]:
        """Copy `game`'s post-move state into the partner row of the same match.
        Returns the partner instance_id to push a frame to (if it has the game
        open), else None."""
        partner = (
            db.query(Game)
            .filter(Game.match_id == game.match_id, Game.id != game.id)
            .first()
        )
        if partner is None:
            return None
        partner.fen = board.fen()
        partner.moves = game.moves
        partner.last_move = game.last_move
        partner.status = game.status
        pc = chess.WHITE if partner.player_color == GameColor.WHITE else chess.BLACK
        partner.is_my_turn = board.turn == pc
        if not partner.instance_id:
            return None
        inst = db.get(Instance, partner.instance_id)
        if (
            inst is not None
            and inst.current_game_id == partner.id
            and inst.current_screen == ScreenType.PLAY
        ):
            return partner.instance_id
        return None

    def _push_frame(self, instance_id: str) -> None:
        """Render the instance's current screen and submit it to LLSS so the
        device shows it on its next poll. (_render_and_submit_frame is async.)"""
        import asyncio

        from hlss.routers.instances import _render_and_submit_frame

        try:
            asyncio.run(_render_and_submit_frame(instance_id))
        except Exception:
            logger.exception("local relay frame push failed for %s", instance_id)


# Module-level instance (stateless; safe to share).
local_backend = LocalBackend()


def is_local(game: Game) -> bool:
    return getattr(game, "backend", BACKEND_LICHESS) == BACKEND_LOCAL
