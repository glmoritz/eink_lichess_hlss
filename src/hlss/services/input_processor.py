"""
Input processor service for handling device button events.
"""

import json
from typing import Optional

import chess
from sqlalchemy.orm import Session

from hlss.models import ButtonType, Game, Instance, ScreenType
from hlss.schemas import MoveState, MoveStateStep


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
        
        # Get games for this instance to determine available play screens
        games = self.db.query(Game).filter(
            Game.status.in_(["created", "started"])
        ).all()

        if instance.current_screen == ScreenType.SETUP:
            # Can't navigate from setup until configured
            return False, "Configure an account first"

        try:
            current_idx = screens.index(instance.current_screen)
            new_idx = (current_idx + direction) % len(screens)
            instance.current_screen = screens[new_idx]
            self.db.commit()
            return True, None
        except ValueError:
            return False, None

    def _handle_setup_input(
        self,
        instance: Instance,
        button: ButtonType,
    ) -> tuple[bool, Optional[str]]:
        """Handle input on the setup screen."""
        # Setup screen doesn't respond to button input
        # Configuration is done via web interface
        return False, None

    def _handle_new_match_input(
        self,
        instance: Instance,
        button: ButtonType,
    ) -> tuple[bool, Optional[str]]:
        """Handle input on the new match screen."""
        # TODO: Implement new match screen input handling
        # BTN_1 - Cycle users
        # BTN_2 - Cycle colors
        # BTN_8 - Show QR code
        # ENTER - Create match
        # ESC - Cancel
        return False, None

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
        move_state = self._load_move_state(instance)
        board = chess.Board(game.fen)

        # Process based on current step
        if move_state.step == MoveStateStep.SELECT_PIECE:
            return self._handle_piece_selection(instance, button, move_state, board)
        elif move_state.step == MoveStateStep.SELECT_FILE:
            return self._handle_file_selection(instance, button, move_state, board)
        elif move_state.step == MoveStateStep.SELECT_RANK:
            return self._handle_rank_selection(instance, button, move_state, board)
        elif move_state.step == MoveStateStep.DISAMBIGUATION:
            return self._handle_disambiguation(instance, button, move_state, board)
        elif move_state.step == MoveStateStep.CONFIRM:
            return self._handle_confirmation(instance, button, move_state, game)

        return False, None

    def _load_move_state(self, instance: Instance) -> MoveState:
        """Load move state from instance or create new one."""
        if instance.move_state:
            try:
                data = json.loads(instance.move_state)
                return MoveState(**data)
            except (json.JSONDecodeError, ValueError):
                pass
        return MoveState()

    def _save_move_state(self, instance: Instance, move_state: MoveState) -> None:
        """Save move state to instance."""
        instance.move_state = json.dumps(move_state.model_dump())
        self.db.commit()

    def _clear_move_state(self, instance: Instance) -> None:
        """Clear move state."""
        instance.move_state = None
        self.db.commit()

    def _handle_piece_selection(
        self,
        instance: Instance,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle piece type selection."""
        if button == ButtonType.ESC:
            self._clear_move_state(instance)
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
                self._save_move_state(instance, move_state)
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
        self._save_move_state(instance, move_state)
        return True, None

    def _handle_file_selection(
        self,
        instance: Instance,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle destination file selection."""
        if button == ButtonType.ESC:
            move_state.step = MoveStateStep.SELECT_PIECE
            move_state.selected_piece = None
            self._save_move_state(instance, move_state)
            return True, None

        file = self.FILE_BUTTONS.get(button)
        if not file:
            return False, None

        # Validate file has legal moves for selected piece
        # (simplified - full implementation would check legal moves)
        move_state.selected_file = file
        move_state.step = MoveStateStep.SELECT_RANK
        self._save_move_state(instance, move_state)
        return True, None

    def _handle_rank_selection(
        self,
        instance: Instance,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle destination rank selection."""
        if button == ButtonType.ESC:
            move_state.step = MoveStateStep.SELECT_FILE
            move_state.selected_file = None
            self._save_move_state(instance, move_state)
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

        self._save_move_state(instance, move_state)
        return True, None

    def _handle_disambiguation(
        self,
        instance: Instance,
        button: ButtonType,
        move_state: MoveState,
        board: chess.Board,
    ) -> tuple[bool, Optional[str]]:
        """Handle move disambiguation."""
        if button == ButtonType.ESC:
            move_state.step = MoveStateStep.SELECT_RANK
            move_state.selected_rank = None
            move_state.disambiguation_options = []
            self._save_move_state(instance, move_state)
            return True, None

        # Map buttons to disambiguation options
        btn_num = int(button.value[-1]) if button.value.startswith("BTN_") else None
        if btn_num and btn_num <= len(move_state.disambiguation_options):
            move_state.pending_move = move_state.disambiguation_options[btn_num - 1]
            move_state.step = MoveStateStep.CONFIRM
            self._save_move_state(instance, move_state)
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
        if button == ButtonType.ESC:
            # Cancel the move
            self._clear_move_state(instance)
            return True, None

        if button == ButtonType.ENTER:
            # Confirm the move - will be sent to Lichess
            # The actual move submission is handled by a separate service
            # For now, just clear the move state
            self._clear_move_state(instance)
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
            buttons[ButtonType.BTN_1] = ("User", True)
            buttons[ButtonType.BTN_2] = ("Color", True)
            buttons[ButtonType.BTN_8] = ("QR Code", True)
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
