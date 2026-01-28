"""
Input processor service for handling device button events.
"""

import json
from datetime import datetime
from typing import Any, Optional

import chess
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hlss.models import (
    Adversary,
    ButtonType,
    Game,
    GameColor,
    GameStatus,
    Instance,
    LichessAccount,
    ScreenType,
)
from hlss.schemas import MoveState, MoveStateStep
from hlss.services.lichess import LichessService
from hlss.services.new_match_state import (
    NEW_MATCH_COLORS,
    load_new_match_state,
    serialize_new_match_state,
)


class InputProcessorService:
    """Service for processing input events and updating state."""

    # Piece selection mapping (BTN_1 through BTN_8)
    PIECE_BUTTONS = {
        ButtonType.BTN_1: "P",  # Pawn
        ButtonType.BTN_2: "N",  # Knight
        ButtonType.BTN_3: "B",  # Bishop
        ButtonType.BTN_4: "R",  # Rook
        ButtonType.BTN_5: "Q",  # Queen
        ButtonType.BTN_6: "K",  # King
        ButtonType.BTN_7: "O-O",  # Kingside castle
        ButtonType.BTN_8: "O-O-O",  # Queenside castle
    }

    # File selection mapping
    FILE_BUTTONS = {
        ButtonType.BTN_1: "a",
        ButtonType.BTN_2: "b",
        ButtonType.BTN_3: "c",
        ButtonType.BTN_4: "d",
        ButtonType.BTN_5: "e",
        ButtonType.BTN_6: "f",
        ButtonType.BTN_7: "g",
        ButtonType.BTN_8: "h",
    }

    # Rank selection mapping
    RANK_BUTTONS = {
        ButtonType.BTN_1: 1,
        ButtonType.BTN_2: 2,
        ButtonType.BTN_3: 3,
        ButtonType.BTN_4: 4,
        ButtonType.BTN_5: 5,
        ButtonType.BTN_6: 6,
        ButtonType.BTN_7: 7,
        ButtonType.BTN_8: 8,
    }

    def __init__(self, db: Session):
        self.db = db

    def process_button(
        self,
        instance: Instance,
        button: ButtonType,
    ) -> tuple[bool, Optional[str]]:
        """
        Process a button press for the given instance.

        Args:
            instance: The HLSS instance
            button: The button that was pressed

        Returns:
            Tuple of (state_changed, error_message)
        """
        # Handle navigation buttons
        if button == ButtonType.HL_LEFT:
            return self._navigate_screen(instance, -1)
        elif button == ButtonType.HL_RIGHT:
            return self._navigate_screen(instance, 1)

        # Handle screen-specific buttons
        if instance.current_screen == ScreenType.SETUP:
            return self._handle_setup_input(instance, button)
        elif instance.current_screen == ScreenType.NEW_MATCH:
            return self._handle_new_match_input(instance, button)
        elif instance.current_screen == ScreenType.PLAY:
            return self._handle_play_input(instance, button)

        return False, None

    def _navigate_screen(self, instance: Instance, direction: int) -> tuple[bool, Optional[str]]:
        """Navigate between screens."""
        screens = [ScreenType.NEW_MATCH, ScreenType.PLAY]

        if instance.current_screen == ScreenType.SETUP:
            # Can't navigate from setup until configured
            if instance.needs_configuration:
                return False, "Configure an account first"

        # Build navigation targets: start with NEW_MATCH, then one entry per active game
        targets: list[str] = ["new_match"]

        # Sync active games if not synced in the last 30 seconds
        if instance.linked_account_id:
            account = self.db.get(LichessAccount, instance.linked_account_id)
            if account:
                now = datetime.utcnow()
                last = getattr(account, "last_games_sync_at", None)
                if not last or (now - last).total_seconds() > 30:
                    try:
                        self.sync_active_games_for_account(account.id)
                        account.last_games_sync_at = now
                        self.db.commit()
                    except Exception:
                        # Don't block navigation on sync failures
                        pass

        # Get current active games for this account
        games = (
            self.db.query(Game)
            .filter(Game.account_id == instance.linked_account_id)
            .filter(Game.status.in_([GameStatus.CREATED, GameStatus.STARTED]))
            .order_by(Game.created_at)
            .all()
        )

        for g in games:
            targets.append(g.id)

        # Determine current position in targets
        if instance.current_screen == ScreenType.NEW_MATCH:
            current_key = "new_match"
        elif instance.current_screen == ScreenType.PLAY and instance.current_game_id:
            current_key = instance.current_game_id
            if current_key not in targets:
                current_key = "new_match"
        else:
            current_key = "new_match"

        try:
            idx = targets.index(current_key)
        except ValueError:
            idx = 0

        new_idx = (idx + direction) % len(targets)
        new_key = targets[new_idx]

        if new_key == "new_match":
            instance.current_screen = ScreenType.NEW_MATCH
            instance.current_game_id = None
        else:
            instance.current_screen = ScreenType.PLAY
            instance.current_game_id = new_key

        self.db.commit()
        return True, None

    def _handle_setup_input(
        self,
        instance: Instance,
        button: ButtonType,
    ) -> tuple[bool, str | None]:
        """Handle input on the setup screen."""
        # Any button press returns to new match screen
        instance.current_screen = ScreenType.NEW_MATCH
        self.db.commit()
        return True, None

    def _handle_new_match_input(
        self,
        instance: Instance,
        button: ButtonType,
    ) -> tuple[bool, Optional[str]]:
        """Handle input on the new match screen."""
        # BTN_1 - Previous adversary
        # BTN_3 - Previous color
        # BTN_6 - Next color
        # BTN_8 - Next adversary
        # ENTER - Create match placeholder
        state = load_new_match_state(instance)

        if button == ButtonType.BTN_1:
            return self._cycle_adversary(instance, state, direction=-1)

        if button == ButtonType.BTN_8:
            return self._cycle_adversary(instance, state, direction=1)

        if button == ButtonType.BTN_3:
            return self._cycle_color(instance, state, direction=-1)

        if button == ButtonType.BTN_6:
            return self._cycle_color(instance, state, direction=1)

        if button == ButtonType.ENTER:
            return self._create_new_match(instance, state)

        if button == ButtonType.ESC:
            return False, None

        return False, None

    def _create_new_match(
        self,
        instance: Instance,
        state: dict[str, Optional[str]],
    ) -> tuple[bool, Optional[str]]:
        """Create a new challenge on Lichess using the configured account."""
        if not instance.linked_account_id:
            return False, "No account linked"

        account = self.db.get(LichessAccount, instance.linked_account_id)
        if not account or not account.api_token:
            return False, "Account not configured"

        adversary_id = state.get("adversary_id")
        if not adversary_id:
            return False, "No adversary selected"

        adversary = self.db.get(Adversary, adversary_id)
        if not adversary:
            return False, "Adversary not found"

        color = state.get("color") or NEW_MATCH_COLORS[0]
        if color not in NEW_MATCH_COLORS:
            color = NEW_MATCH_COLORS[0]

        lichess = LichessService(account.api_token)
        try:
            challenge = lichess.create_challenge(
                username=adversary.lichess_username,
                color=color,
            )
        except Exception as exc:
            err = str(exc)
            return False, f"Failed to create challenge: {err[:80]}"

        friendly_name = adversary.friendly_name or adversary.lichess_username
        challenge_id: Optional[str] = None
        if isinstance(challenge, dict):
            challenge_obj = challenge.get("challenge") or challenge
            if isinstance(challenge_obj, dict):
                challenge_id = challenge_obj.get("id")

        message = f"Challenge sent to {friendly_name}"
        if challenge_id:
            message = f"Challenge {challenge_id} sent to {friendly_name}"

        return True, message

    def sync_active_games_for_account(self, account_id: str) -> int:
        """Fetch ongoing Lichess games for the account and persist them."""
        account = self.db.get(LichessAccount, account_id)
        if not account or not account.api_token:
            return 0

        lichess = LichessService(account.api_token)
        ongoing = lichess.get_ongoing_games()
        sync_count = 0

        for game_data in ongoing:
            lichess_id = game_data.get("fullId")
            if not lichess_id:
                continue

            stmt = select(Game).where(Game.lichess_game_id == lichess_id)
            existing_game = self.db.scalar(stmt)

            player_color = self._determine_player_color(account.username, game_data)
            opponent_username = self._determine_opponent_username(account.username, game_data)
            status = self._map_game_status(game_data.get("status"))
            is_my_turn = bool(game_data.get("isMyTurn"))
            # Prefer the realtime stream to get the latest moves/fen
            fen = game_data.get("fen", "")
            last_move = game_data.get("lastMove")
            moves = game_data.get("moves")

            incoming_initial_fen = None
            try:
                stream = lichess.get_game_stream(lichess_id)
                # The stream yields the current game state as the first item
                try:
                    state = next(stream)
                except StopIteration:
                    state = None
                if isinstance(state, dict):
                    # common keys may be at top-level or under 'state'
                    moves = state["state"]["moves"]
                    incoming_initial_fen = state.get("initialFen")
                    # lastMove may be provided; otherwise derive from moves
                    if not last_move and moves:
                        parts = moves.split()
                        last_move = parts[-1] if parts else ""
            except Exception:
                # If stream fails, fall back to provided game_data
                pass

            if existing_game:
                existing_game.account_id = account.id
                existing_game.player_color = player_color
                existing_game.opponent_username = opponent_username or "Unknown"
                existing_game.status = status
                existing_game.is_my_turn = is_my_turn
                existing_game.fen = fen
                existing_game.last_move = last_move

                # Merge moves / initial fen intelligently. If the incoming moves overlap
                # with the stored moves, keep the stored initial_fen and merge the moves.
                # Otherwise, adopt the incoming initial_fen and moves.
                try:
                    if incoming_initial_fen:
                        new_initial, new_moves = self._merge_moves_and_initial_fen(
                            existing_game.initial_fen,
                            existing_game.moves or "",
                            incoming_initial_fen,
                            moves or "",
                        )
                    else:
                        new_initial = fen
                        new_moves = moves
                except Exception:
                    # On any failure, fall back to replacing with incoming values
                    new_initial, new_moves = incoming_initial_fen, moves

                # Only update initial_fen if _merge_ decided to change it
                existing_game.initial_fen = new_initial
                existing_game.moves = new_moves
                # Save full raw JSON for inspection
                try:
                    existing_game.raw_json = json.dumps(game_data)
                except Exception:
                    existing_game.raw_json = None
            else:
                new_game = Game(
                    lichess_game_id=lichess_id,
                    account_id=account.id,
                    player_color=player_color,
                    opponent_username=opponent_username,
                    status=status,
                    is_my_turn=is_my_turn,
                    fen=fen,
                    initial_fen=(
                        incoming_initial_fen
                        if incoming_initial_fen != "startpos"
                        else "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
                    ),
                    last_move=last_move,
                    moves=moves,
                    raw_json=(json.dumps(game_data) if isinstance(game_data, dict) else None),
                )
                self.db.add(new_game)

            sync_count += 1

        if ongoing:
            self.db.commit()

        return sync_count

    def _determine_player_color(self, username: str, game_data: dict[str, Any]) -> GameColor:
        if game_data["color"].lower() == "white":
            return GameColor.WHITE
        elif game_data["color"].lower() == "black":
            return GameColor.BLACK
        return GameColor.WHITE

    def _determine_opponent_username(self, username: str, game_data: dict[str, Any]) -> str | None:
        return game_data["opponent"]["id"] or game_data["opponent"]["username"]

    def _map_game_status(self, status_value: Optional[str]) -> GameStatus:
        mapping = {
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
            "noStart": GameStatus.NO_START,
            "variantEnd": GameStatus.VARIANT_END,
            "unknownFinish": GameStatus.UNKNOWN_FINISH,
        }
        if not status_value:
            return GameStatus.UNKNOWN_FINISH
        return mapping.get(status_value["name"].lower(), GameStatus.UNKNOWN_FINISH)

    def _merge_moves_and_initial_fen(
        self,
        existing_initial_fen: Optional[str],
        existing_moves: str,
        incoming_initial_fen: Optional[str],
        incoming_moves: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """Merge existing and incoming moves, deciding whether to keep initial_fen.

        If incoming moves overlap with the tail of existing moves we keep the
        existing_initial_fen and merge the sequences. Otherwise we adopt the
        incoming initial fen and move list.
        """
        # Normalize to lists
        existing_list = (existing_moves or "").split()
        incoming_list = (incoming_moves or "").split()

        if not incoming_list:
            return existing_initial_fen, existing_moves

        if not existing_list:
            return incoming_initial_fen, incoming_moves

        # Fast overlap check using list equality on suffix/prefix
        max_overlap = min(len(existing_list), len(incoming_list))
        for overlap in range(max_overlap, 0, -1):
            if existing_list[-overlap:] == incoming_list[:overlap]:
                merged = existing_list + incoming_list[overlap:]
                return existing_initial_fen, " ".join(merged)

        # No simple overlap found. As a secondary check, try to use chess to
        # verify whether applying incoming moves from its initial_fen leads to
        # a position that appears inside the existing move sequence. This is
        # more expensive and may fail for malformed data; if it fails, fall
        # back to replacing with incoming values.
        try:
            # Build board for incoming moves
            b_in = chess.Board(incoming_initial_fen) if incoming_initial_fen else chess.Board()
            for m in incoming_list:
                try:
                    b_in.push_uci(m)
                except Exception:
                    # If a move cannot be applied as UCI, abort this strategy
                    raise

            # Build board for existing moves
            b_ex = chess.Board(existing_initial_fen) if existing_initial_fen else chess.Board()
            for m in existing_list:
                try:
                    b_ex.push_uci(m)
                except Exception:
                    raise

            # If the final FENs match, we can merge by picking the longer sequence
            if b_in.fen() == b_ex.fen():
                # Pick the longer move list
                if len(existing_list) >= len(incoming_list):
                    return existing_initial_fen, existing_moves
                else:
                    return incoming_initial_fen, incoming_moves
        except Exception:
            # Fall back: replace with incoming if no overlap detected
            pass

        return incoming_initial_fen, incoming_moves

    def _cycle_color(
        self,
        instance: Instance,
        state: dict[str, Optional[str]],
        direction: int,
    ) -> tuple[bool, Optional[str]]:
        """Cycle the selected color."""
        try:
            idx = NEW_MATCH_COLORS.index(state.get("color", NEW_MATCH_COLORS[0]))
        except ValueError:
            idx = 0
        state["color"] = NEW_MATCH_COLORS[(idx + direction) % len(NEW_MATCH_COLORS)]
        self._save_new_match_state(instance, state)
        return True, None

    def _cycle_adversary(
        self,
        instance: Instance,
        state: dict[str, Optional[str]],
        direction: int,
    ) -> tuple[bool, Optional[str]]:
        """Cycle through the configured adversaries for this account."""
        if not instance.linked_account_id:
            return False, "No account linked"

        adversaries = self._get_adversaries_for_account(instance.linked_account_id)
        if not adversaries:
            return False, "No adversaries configured"

        selected = self._select_adversary(adversaries, state.get("adversary_id"))
        idx = adversaries.index(selected)
        state["adversary_id"] = adversaries[(idx + direction) % len(adversaries)].id
        self._save_new_match_state(instance, state)
        return True, None

    def _select_adversary(
        self,
        adversaries: list[Adversary],
        selected_id: Optional[str],
    ) -> Adversary:
        """Return the matching adversary or fallback to the first in the list."""
        if not adversaries:
            raise ValueError("adversary list is empty")
        selected = next((adv for adv in adversaries if adv.id == selected_id), None)
        return selected or adversaries[0]

    def _get_adversaries_for_account(self, account_id: str) -> list[Adversary]:
        """Fetch the friends/adversaries for the given account."""
        # If adversary list hasn't been synced in the last minute, refresh it.
        latest_ts = self.db.scalar(
            select(func.max(Adversary.updated_at)).where(Adversary.account_id == account_id)
        )

        # If there is no timestamp (no adversaries) or it's older than 60s, sync.
        if not latest_ts or (datetime.utcnow() - latest_ts).total_seconds() > 60:
            account = self.db.get(LichessAccount, account_id)
            if account:
                try:
                    # Local import to avoid circular imports at module import time
                    from hlss.routers.configure import _sync_account_adversaries

                    _sync_account_adversaries(self.db, account)
                except Exception:
                    # Swallow sync errors and continue with whatever data we have
                    pass

        stmt = (
            select(Adversary)
            .where(Adversary.account_id == account_id)
            .order_by(Adversary.friendly_name)
        )
        return list(self.db.scalars(stmt).all())

    def _load_new_match_state(self, instance: Instance) -> dict[str, Optional[str]]:
        """Load new match selection state from the instance."""
        return load_new_match_state(instance)

    def _save_new_match_state(self, instance: Instance, state: dict[str, Optional[str]]) -> None:
        """Persist new match selection state."""
        instance.new_match_state = serialize_new_match_state(state)
        self.db.commit()

    def _handle_play_input(
        self,
        instance: Instance,
        button: ButtonType,
    ) -> tuple[bool, Optional[str]]:
        """Handle input on the play screen."""
        if not instance.current_game_id:
            return False, "No active game"

        game = self.db.get(Game, instance.current_game_id)
        if not game:
            return False, "Game not found"

        if not game.is_my_turn:
            return False, "Not your turn"

        # Load or create move state
        move_state = self._load_move_state(game)
        board = chess.Board(game.fen)

        # Process based on current step
        if move_state.step == MoveStateStep.SELECT_PIECE:
            return self._handle_piece_selection(instance, game, button, move_state, board)
        elif move_state.step == MoveStateStep.SELECT_FILE:
            return self._handle_file_selection(instance, game, button, move_state, board)
        elif move_state.step == MoveStateStep.SELECT_RANK:
            return self._handle_rank_selection(instance, game, button, move_state, board)
        elif move_state.step == MoveStateStep.DISAMBIGUATION:
            return self._handle_disambiguation(instance, game, button, move_state, board)
        elif move_state.step == MoveStateStep.CONFIRM:
            return self._handle_confirmation(instance, button, move_state, game)

        return False, None

    def _load_move_state(self, game: Game) -> MoveState:
        """Load move state from the game or create a new one."""
        if game.move_state:
            try:
                data = json.loads(game.move_state)
                if isinstance(data, dict):
                    return MoveState(**data)
            except (json.JSONDecodeError, ValueError):
                pass
        return MoveState()

    def _save_move_state(self, game: Game, move_state: MoveState) -> None:
        """Save move state to the game."""
        game.move_state = json.dumps(move_state.model_dump())
        self.db.commit()

    def _clear_move_state(self, game: Game) -> None:
        """Clear move state from the game."""
        game.move_state = None
        self.db.commit()

    def _handle_piece_selection(
        self,
        instance: Instance,
        game: Game,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle piece type selection."""
        if button == ButtonType.ESC:
            self._clear_move_state(game)
            return True, None

        piece = self.PIECE_BUTTONS.get(button)
        if not piece:
            return False, None

        # Handle castling
        if piece in ["O-O", "O-O-O"]:
            # Check if castling is legal
            castle_move = None
            for move in board.legal_moves:
                if board.is_castling(move):
                    san = board.san(move)
                    if (piece == "O-O" and san in ["O-O", "0-0"]) or (
                        piece == "O-O-O" and san in ["O-O-O", "0-0-0"]
                    ):
                        castle_move = move
                        break

            if castle_move:
                move_state.pending_move = castle_move.uci()
                move_state.step = MoveStateStep.CONFIRM
                self._save_move_state(game, move_state)
                return True, None
            else:
                return False, "Castling not legal"

        # Check if any legal move exists for this piece type
        piece_type = chess.PIECE_SYMBOLS.index(piece.lower())
        has_moves = any(
            board.piece_at(move.from_square)
            and board.piece_at(move.from_square).piece_type == piece_type
            for move in board.legal_moves
        )

        if not has_moves:
            return False, f"No legal moves for {piece}"

        move_state.selected_piece = piece
        move_state.step = MoveStateStep.SELECT_FILE
        self._save_move_state(game, move_state)
        return True, None

    def _handle_file_selection(
        self,
        instance: Instance,
        game: Game,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle destination file selection."""
        if button == ButtonType.ESC:
            move_state.step = MoveStateStep.SELECT_PIECE
            move_state.selected_piece = None
            self._save_move_state(game, move_state)
            return True, None

        file = self.FILE_BUTTONS.get(button)
        if not file:
            return False, None

        # Validate file has legal moves for selected piece
        # (simplified - full implementation would check legal moves)
        move_state.selected_file = file
        move_state.step = MoveStateStep.SELECT_RANK
        self._save_move_state(game, move_state)
        return True, None

    def _handle_rank_selection(
        self,
        instance: Instance,
        game: Game,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle destination rank selection."""
        if button == ButtonType.ESC:
            move_state.step = MoveStateStep.SELECT_FILE
            move_state.selected_file = None
            self._save_move_state(game, move_state)
            return True, None

        rank = self.RANK_BUTTONS.get(button)
        if not rank:
            return False, None

        move_state.selected_rank = rank
        target_square = f"{move_state.selected_file}{rank}"

        # Find legal moves to this square with selected piece
        piece_type = chess.PIECE_SYMBOLS.index(move_state.selected_piece.lower())
        matching_moves = [
            move
            for move in board.legal_moves
            if (
                chess.square_name(move.to_square) == target_square
                and board.piece_at(move.from_square)
                and board.piece_at(move.from_square).piece_type == piece_type
            )
        ]

        if not matching_moves:
            return False, "Invalid move"

        if len(matching_moves) == 1:
            # Unambiguous move
            move_state.pending_move = matching_moves[0].uci()
            move_state.step = MoveStateStep.CONFIRM
        else:
            # Need disambiguation
            move_state.disambiguation_options = [m.uci() for m in matching_moves]
            move_state.step = MoveStateStep.DISAMBIGUATION

        self._save_move_state(game, move_state)
        return True, None

    def _handle_disambiguation(
        self,
        instance: Instance,
        game: Game,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle move disambiguation."""
        if button == ButtonType.ESC:
            move_state.step = MoveStateStep.SELECT_RANK
            move_state.selected_rank = None
            move_state.disambiguation_options = []
            self._save_move_state(game, move_state)
            return True, None

        # Map buttons to disambiguation options
        btn_num = int(button.value[-1]) if button.value.startswith("BTN_") else None
        if btn_num and btn_num <= len(move_state.disambiguation_options):
            move_state.pending_move = move_state.disambiguation_options[btn_num - 1]
            move_state.step = MoveStateStep.CONFIRM
            self._save_move_state(game, move_state)
            return True, None

        return False, None

    def _handle_confirmation(
        self,
        instance: Instance,
        button: ButtonType,
        move_state: MoveState,
        game: Game,
    ) -> tuple[bool, Optional[str]]:
        """Handle move confirmation."""
        if button == ButtonType.BTN_8:
            # Cancel the move
            self._clear_move_state(game)
            return True, None

        if button == ButtonType.BTN_1:
            # Confirm the move - will be sent to Lichess
            # The actual move submission is handled by a separate service
            # For now, just clear the move state
            self._clear_move_state(game)
            return True, move_state.pending_move  # Return the move to be executed

        return False, None

    def get_valid_buttons(
        self,
        instance: Instance,
    ) -> dict[ButtonType, tuple[str, bool]]:
        """
        Get valid buttons for the current state.

        Returns:
            Dict mapping button to (label, enabled) tuple
        """
        buttons: dict[ButtonType, tuple[str, bool]] = {}

        if instance.current_screen == ScreenType.SETUP:
            buttons[ButtonType.BTN_8] = ("Config", True)

        elif instance.current_screen == ScreenType.NEW_MATCH:
            user_enabled = bool(
                instance.linked_account_id
                and self._get_adversaries_for_account(instance.linked_account_id)
            )
            color_enabled = True
            buttons[ButtonType.BTN_1] = ("Prev Opp", user_enabled)
            buttons[ButtonType.BTN_3] = ("Prev Color", color_enabled)
            buttons[ButtonType.BTN_6] = ("Next Color", color_enabled)
            buttons[ButtonType.BTN_8] = ("Next Opp", user_enabled)
            buttons[ButtonType.ENTER] = ("Create", True)
            buttons[ButtonType.ESC] = ("Cancel", True)

        elif instance.current_screen == ScreenType.PLAY:
            if not instance.current_game_id:
                return buttons

            game = self.db.get(Game, instance.current_game_id)
            if not game or not game.is_my_turn:
                return buttons

            move_state = self._load_move_state(instance)
            board = chess.Board(game.fen)

            if move_state.step == MoveStateStep.SELECT_PIECE:
                buttons = self._get_piece_buttons(board)
            elif move_state.step == MoveStateStep.SELECT_FILE:
                buttons = self._get_file_buttons(board, move_state)
            elif move_state.step == MoveStateStep.SELECT_RANK:
                buttons = self._get_rank_buttons(board, move_state)
            elif move_state.step == MoveStateStep.DISAMBIGUATION:
                buttons = self._get_disambiguation_buttons(move_state)
            elif move_state.step == MoveStateStep.CONFIRM:
                buttons[ButtonType.ENTER] = ("Confirm", True)
                buttons[ButtonType.ESC] = ("Cancel", True)

        # Always add navigation
        buttons[ButtonType.HL_LEFT] = ("←", True)
        buttons[ButtonType.HL_RIGHT] = ("→", True)

        return buttons

    def _get_piece_buttons(self, board: chess.Board) -> dict[ButtonType, tuple[str, bool]]:
        """Get available piece selection buttons."""
        buttons: dict[ButtonType, tuple[str, bool]] = {}

        # Check each piece type
        for btn, piece in self.PIECE_BUTTONS.items():
            if piece in ["O-O", "O-O-O"]:
                # Check castling availability
                has_castle = any(
                    board.is_castling(m)
                    and (
                        (piece == "O-O" and board.is_kingside_castling(m))
                        or (piece == "O-O-O" and board.is_queenside_castling(m))
                    )
                    for m in board.legal_moves
                )
                buttons[btn] = (piece, has_castle)
            else:
                piece_type = chess.PIECE_SYMBOLS.index(piece.lower())
                has_moves = any(
                    board.piece_at(m.from_square)
                    and board.piece_at(m.from_square).piece_type == piece_type
                    for m in board.legal_moves
                )
                buttons[btn] = (piece, has_moves)

        buttons[ButtonType.ESC] = ("Cancel", True)
        return buttons

    def _get_file_buttons(
        self,
        board: chess.Board,
        move_state: MoveState,
    ) -> dict[ButtonType, tuple[str, bool]]:
        """Get available file selection buttons."""
        buttons: dict[ButtonType, tuple[str, bool]] = {}

        for btn, file in self.FILE_BUTTONS.items():
            # Simplified: show all files as available
            # Full implementation would check which files have valid moves
            buttons[btn] = (file.upper(), True)

        buttons[ButtonType.ESC] = ("Back", True)
        return buttons

    def _get_rank_buttons(
        self,
        board: chess.Board,
        move_state: MoveState,
    ) -> dict[ButtonType, tuple[str, bool]]:
        """Get available rank selection buttons."""
        buttons: dict[ButtonType, tuple[str, bool]] = {}

        for btn, rank in self.RANK_BUTTONS.items():
            buttons[btn] = (str(rank), True)

        buttons[ButtonType.ESC] = ("Back", True)
        return buttons

    def _get_disambiguation_buttons(
        self,
        move_state: MoveState,
    ) -> dict[ButtonType, tuple[str, bool]]:
        """Get disambiguation option buttons."""
        buttons: dict[ButtonType, tuple[str, bool]] = {}

        for i, option in enumerate(move_state.disambiguation_options):
            btn = ButtonType(f"BTN_{i + 1}")
            buttons[btn] = (option, True)

        buttons[ButtonType.ESC] = ("Back", True)
        return buttons
