"""
Renderer service for generating e-Ink display frames.
"""

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Optional

import cairosvg
import chess
import chess.svg
import qrcode
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from hlss.config import get_settings
from hlss.schemas import ButtonAction, MoveState, MoveStateStep, ScreenType
from hlss.services.html_renderer import render_html_file_to_png


class RendererService:
    """Service for rendering screens as PNG frames for e-Ink displays."""

    # Default colors for monochrome e-Ink
    WHITE = (255, 255, 255)
    BLACK = (0, 0, 0)
    LIGHT_GRAY = (210, 210, 210)
    MID_GRAY = (150, 150, 150)
    DARK_GRAY = (80, 80, 80)
    GRAY = MID_GRAY

    HEADER_HEIGHT = 50
    FOOTER_HEIGHT = 50
    SCREEN_HEIGHT = 480
    SCREEN_WIDTH = 800
    MARGIN = 10

    PIECE_TYPE_MAP = {
        "P": chess.PAWN,
        "N": chess.KNIGHT,
        "B": chess.BISHOP,
        "R": chess.ROOK,
        "Q": chess.QUEEN,
        "K": chess.KING,
    }

    # Chess piece unicode symbols
    PIECE_SYMBOLS = {
        "P": "♙",
        "N": "♘",
        "B": "♗",
        "R": "♖",
        "Q": "♕",
        "K": "♔",
        "p": "♟",
        "n": "♞",
        "b": "♝",
        "r": "♜",
        "q": "♛",
        "k": "♚",
    }

    PIECE_SVG = {chess.WHITE: {}, chess.BLACK: {}}
    PIECE_SMALL_SVG = {chess.WHITE: {}, chess.BLACK: {}}

    def __init__(self):
        def clean_svg(svg: str) -> str:
            svg = svg.replace('<?xml version="1.0" encoding="UTF-8"?>', "")
            svg = svg.replace("\n", "")
            return svg.strip()

        for piece_type in self.PIECE_TYPE_MAP.values():
            for player_color in [chess.WHITE, chess.BLACK]:
                i = piece_type - 1  # 0-based index
                piece = chess.Piece(piece_type, player_color)

                self.PIECE_SVG[piece.color][f"{piece.symbol().upper()}"] = clean_svg(
                    chess.svg.piece(piece)
                )
                self.PIECE_SMALL_SVG[piece.color][f"{piece.symbol().upper()}"] = clean_svg(
                    chess.svg.piece(piece, size=18)
                )

        self.settings = get_settings()
        self.width = self.settings.default_display_width
        self.height = self.settings.default_display_height

        # Try to load a suitable font
        self._font: Optional[ImageFont.FreeTypeFont] = None
        self._font_small: Optional[ImageFont.FreeTypeFont] = None
        self._font_large: Optional[ImageFont.FreeTypeFont] = None
        self._load_fonts()

    def _load_fonts(self) -> None:
        """Load fonts for rendering text."""
        # Common font paths to try
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]

        for path in font_paths:
            if Path(path).exists():
                try:
                    self._font = ImageFont.truetype(path, 16)
                    self._font_small = ImageFont.truetype(path, 12)
                    self._font_large = ImageFont.truetype(path, 24)
                    return
                except OSError:
                    continue

        # Fall back to default font
        self._font = ImageFont.load_default()
        self._font_small = self._font
        self._font_large = self._font

    def _create_base_image(self) -> Image.Image:
        """Create a base white image."""
        return Image.new("RGB", (self.width, self.height), self.WHITE)

    def _content_bounds(self) -> tuple[int, int, int, int]:
        """Get the drawable content bounds between header and footer."""
        top = self.HEADER_HEIGHT + self.MARGIN
        bottom = self.height - self.FOOTER_HEIGHT - self.MARGIN
        left = self.MARGIN
        right = self.width - self.MARGIN
        return left, top, right, bottom

    def _render_header(self, draw: ImageDraw.ImageDraw, title: str) -> None:
        """Render the consistent header with nav and enter/esc hints."""
        draw.rectangle([0, 0, self.width, self.HEADER_HEIGHT], fill=self.LIGHT_GRAY)
        draw.line(
            [0, self.HEADER_HEIGHT - 1, self.width, self.HEADER_HEIGHT - 1], fill=self.DARK_GRAY
        )

        # Title
        title_x = self.SCREEN_HEIGHT / 2
        title_y = self.HEADER_HEIGHT / 2
        draw.text((title_x, title_y), title, fill=self.BLACK, font=self._font_large, align="center")

        # HL left/right buttons (top-left)
        button_width = self.HEADER_HEIGHT - 10
        button_height = button_width

        self._draw_header_button(
            draw,
            self.MARGIN + button_width + self.MARGIN,
            self.MARGIN,
            button_width,
            button_height,
            "◀",
            self.WHITE,
        )

        self._draw_header_button(
            draw,
            self.MARGIN + button_width + self.MARGIN,
            self.MARGIN,
            button_width,
            button_height,
            "▶",
            self.WHITE,
        )

        enter_width = self.HEADER_HEIGHT * 3
        enter_height = button_width

        # ENTER/ESC buttons (top-right)
        self._draw_header_button(
            draw,
            self.SCREEN_WIDTH - self.MARGIN - enter_width - self.MARGIN - enter_width - self.MARGIN,
            self.MARGIN,
            enter_width,
            enter_height,
            "ENTER",
            self.DARK_GRAY,
        )

        self._draw_header_button(
            draw,
            self.SCREEN_WIDTH - self.MARGIN - enter_width - self.MARGIN,
            self.MARGIN,
            enter_width,
            enter_height,
            "VOLTAR",
            self.WHITE,
        )

    def _draw_header_button(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        width: int,
        height: int,
        label: str = "",
        fill: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        """Draw a small header button with an icon and label."""
        draw.rounded_rectangle(
            [x, y, x + width, y + height], radius=6, outline=self.DARK_GRAY, fill=fill
        )
        draw.text(
            ((x + width) / 2, (y + height) / 2),
            label,
            fill=self.BLACK,
            font=self._font,
            align="center",
        )

    def _render_context_bar(
        self,
        draw: ImageDraw.ImageDraw,
        button_actions: list[ButtonAction],
    ) -> dict[str, tuple[int, int]]:
        """Render the bottom context bar for 8 buttons.

        Returns mapping of button value to its center coordinates.
        """
        draw.rectangle(
            [0, self.height - self.FOOTER_HEIGHT, self.width, self.height],
            fill=self.LIGHT_GRAY,
        )
        draw.line(
            [0, self.height - self.FOOTER_HEIGHT, self.width, self.height - self.FOOTER_HEIGHT],
            fill=self.DARK_GRAY,
        )

        # Map provided actions
        action_map = {action.button.value: action for action in button_actions}

        bar_top = self.height - self.FOOTER_HEIGHT + 12
        bar_height = self.FOOTER_HEIGHT - 24
        slot_width = (self.width - 2 * self.MARGIN) // 8
        centers: dict[str, tuple[int, int]] = {}

        for i in range(8):
            btn_value = f"BTN_{i + 1}"
            x0 = self.MARGIN + i * slot_width
            x1 = x0 + slot_width - 8
            y0 = bar_top
            y1 = bar_top + bar_height
            action = action_map.get(btn_value)
            enabled = action.enabled if action else True
            label = action.label if action else ""

            outline = self.DARK_GRAY
            fill = self.WHITE if enabled else self.LIGHT_GRAY
            text_color = self.BLACK if enabled else self.MID_GRAY

            draw.rounded_rectangle([x0, y0, x1, y1], radius=10, outline=outline, fill=fill)
            draw.text((x0 + 8, y0 + 8), str(i + 1), fill=self.DARK_GRAY, font=self._font_small)

            if label:
                text = label[:10]
                draw.text((x0 + 8, y0 + 30), text, fill=text_color, font=self._font_small)

            centers[btn_value] = ((x0 + x1) // 2, (y0 + y1) // 2)

        return centers

    def _draw_curve(
        self,
        draw: ImageDraw.ImageDraw,
        start: tuple[int, int],
        end: tuple[int, int],
        height: int,
        color: tuple[int, int, int],
        width: int = 2,
    ) -> None:
        """Draw a soft curved connector between two points."""
        mid_x = (start[0] + end[0]) // 2
        mid_y = min(start[1], end[1]) - height
        points = [
            start,
            (mid_x, mid_y),
            end,
        ]
        draw.line(points, fill=color, width=width, joint="curve")

    def render_setup_screen(self, config_url: str) -> bytes:
        """
        Render the initial setup screen with QR code.

        Args:
            config_url: URL for web configuration

        Returns:
            PNG image data
        """
        img = self._create_base_image()
        draw = ImageDraw.Draw(img)

        self._render_header(draw, "Setup")
        self._render_context_bar(draw, [])
        left, top, right, bottom = self._content_bounds()

        instructions = [
            "No Lichess account configured.",
            "",
            "Scan the QR code or visit:",
            config_url,
            "",
            "to configure your account.",
        ]

        y = top + 8
        for line in instructions:
            draw.text((left, y), line, fill=self.BLACK, font=self._font)
            y += 22

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=6,
            border=2,
        )
        qr.add_data(config_url)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img = qr_img.convert("RGB")

        qr_x = right - qr_img.width
        qr_y = top + (bottom - top - qr_img.height) // 2
        img.paste(qr_img, (qr_x, qr_y))

        return self._image_to_bytes(img)

    def render_new_match_screen(
        self,
        username: str,
        selected_adversary: str,
        selected_color: str,
        button_actions: list[str],
    ) -> bytes:
        """
        Render the new match creation screen.

        Args:
            selected_user: Currently selected Lichess username
            selected_color: Selected color (white/black/random)
            button_actions: List of button action mappings

        Returns:
            PNG image data
        """
        names_pt_br = {"white": "Brancas", "black": "Pretas", "random": "Sorteio"}

        replacements = {
            "@@ADVERSARY@@": selected_adversary,
            "@@PLAYERNAME@@": username,
            "@@PLAYERCOLOR@@": names_pt_br[selected_color.lower()],
            "@@HELPER_TEXT@@": "Use ◀ ▶ para selecionar • ENTER para criar o jogo",
            "@@B1@@": button_actions[0] if len(button_actions) > 0 else " ",
            "@@B2@@": button_actions[1] if len(button_actions) > 1 else " ",
            "@@B3@@": button_actions[2] if len(button_actions) > 2 else " ",
            "@@B4@@": button_actions[3] if len(button_actions) > 3 else " ",
            "@@B5@@": button_actions[5] if len(button_actions) > 4 else " ",
            "@@B6@@": button_actions[6] if len(button_actions) > 5 else " ",
            "@@B7@@": button_actions[7] if len(button_actions) > 6 else " ",
            "@@B8@@": button_actions[8] if len(button_actions) > 8 else " ",
            "@@B9@@": button_actions[9] if len(button_actions) > 9 else " ",
            "@@B10@@": button_actions[10] if len(button_actions) > 10 else " ",
        }
        return render_html_file_to_png(
            "/app/new_match_screen.html",
            width=self.width,
            height=self.height,
            replacements=replacements,
        )

    def render_play_screen(self, game_id: str, player_name: str, db: Session) -> bytes:
        """
        Render the game play screen.

        Args:
            game_id: The id of the game to render
            player_name: Player's username
            db: Database session

        Returns:
            PNG image data
        """
        import chess

        from hlss.models import Game

        game = db.get(Game, game_id)
        if game:
            pos = (
                game.initial_fen
                if (game.initial_fen and game.initial_fen != "startpos")
                else chess.STARTING_FEN
            )
            board = chess.Board(pos)
            player_color = chess.WHITE if game.player_color.value == "white" else chess.BLACK
            opponent_name = game.opponent_username or "Unknown"

            # Helper to replace piece letter with unicode symbol
            def san_with_svg(san: str, color: chess.Color = chess.WHITE, big: bool = False) -> str:
                if not san:
                    return san

                # Castling
                if san.startswith("O-O"):
                    return san

                out = san

                # 1) Leading piece
                first = out[0]
                if first in "KQRBN":
                    out = (
                        self.PIECE_SMALL_SVG[color][first]
                        if not big
                        else self.PIECE_SVG[color][first]
                    ) + out[1:]

                # 2) Promotion piece (after '=')
                if "=" in out:
                    base, promo = out.split("=", 1)
                    promo_piece = promo[0]
                    if promo_piece in "QRBN":
                        promo_svg = (
                            self.PIECE_SMALL_SVG[color][promo_piece]
                            if not big
                            else self.PIECE_SVG[color][promo_piece]
                        )
                        out = base + "=" + promo_svg + promo[1:]

                return out

            moves = []

            current_move_number = board.fullmove_number
            white_play = None
            black_play = None

            for uci in (game.moves or "").split():  # assuming this is an ordered list of UCI moves
                move = chess.Move.from_uci(uci)

                if move not in board.legal_moves:
                    raise ValueError(f"Illegal move {uci} at move {current_move_number}")

                san = board.san(move)
                san = san_with_svg(san, board.turn)

                if board.turn == chess.WHITE:
                    white_play = san
                else:
                    black_play = san

                board.push(move)

                # When Black has just moved, the full move is complete
                if board.turn == chess.WHITE:
                    moves.append((current_move_number, white_play, black_play))
                    current_move_number += 1
                    white_play = None
                    black_play = None

            # If the game ended after White's move
            if white_play is not None:
                moves.append((current_move_number, white_play, None))

            from hlss.routers.instances import _deserialize_move_state

            move_state = _deserialize_move_state(game.move_state)

            # Render the static HTML base first, then overlay the SVG board.
            # Prepare simple replacements for the HTML template.

            # SELECT_PIECE = "select_piece"
            #     SELECT_FILE = "select_file"
            #     SELECT_RANK = "select_rank"
            #     DISAMBIGUATION = "disambiguation"
            #     CONFIRM = "confirm"

            replacements = {"@@ADVERSARY@@": opponent_name, "@@USER@@": player_name}

            # calcula o número de peças capturadas
            initial = {
                chess.PAWN: 8,
                chess.KNIGHT: 2,
                chess.BISHOP: 2,
                chess.ROOK: 2,
                chess.QUEEN: 1,
            }

            values = {
                chess.PAWN: 1,
                chess.KNIGHT: 3,
                chess.BISHOP: 3,
                chess.ROOK: 5,
                chess.QUEEN: 9,
            }

            # Helper to build captured pieces string
            def build_captured_str(captured_dict, color):
                symbols = []
                for piece_type in captured_dict.keys():
                    count = captured_dict[piece_type]
                    if count > 0:
                        symbol = self.PIECE_SMALL_SVG[color][
                            chess.Piece(piece_type, color).symbol().upper()
                        ]
                        if piece_type == chess.PAWN and count > 2:
                            symbols.append(f"{count}{symbol}")
                        else:
                            symbols.extend([symbol] * count)
                return " ".join(symbols)

            captured = {
                chess.PAWN: 0,
                chess.KNIGHT: 0,
                chess.BISHOP: 0,
                chess.ROOK: 0,
                chess.QUEEN: 0,
            }
            captured_count = {}
            captured_count[chess.WHITE] = captured.copy()
            captured_count[chess.BLACK] = captured.copy()

            for piece_color in [chess.WHITE, chess.BLACK]:
                for piece_type, count in initial.items():
                    captured_count[piece_color][piece_type] = count - len(
                        board.pieces(piece_type, piece_color)
                    )

            user_captured_str = build_captured_str(captured_count[player_color], player_color)
            adversary_color = not player_color
            adversary_captured_str = build_captured_str(
                captured_count[adversary_color], adversary_color
            )

            # Calculate advantage
            user_adv = sum(captured_count[player_color][pt] * values[pt] for pt in values)
            adversary_adv = sum(captured_count[adversary_color][pt] * values[pt] for pt in values)
            advantage = user_adv - adversary_adv
            if advantage > 0:
                user_captured_str += f" (+{advantage})"
            elif advantage < 0:
                adversary_captured_str += f" (+{abs(advantage)})"

            replacements["@@USER_CAPTURED@@"] = (
                adversary_captured_str if adversary_captured_str else "     "
            )
            replacements["@@ADVERSARY_CAPTURED@@"] = (
                user_captured_str if user_captured_str else "     "
            )

            # Each move entry is a pair: (move number, white move, black move)
            num_entries = 6
            for i in range(1, 8 + 1):
                replacements[f"@@N{i}@@"] = f"{board.fullmove_number +  i - 1}"

                replacements[f"@@WP{i}@@"] = "   "
                replacements[f"@@BP{i}@@"] = "   "

            move_start = len(moves) - num_entries if len(moves) > num_entries else 0
            total_moves = len(moves) - move_start
            for i in range(1, total_moves + 1):
                replacements[f"@@N{i}@@"] = (
                    str(moves[move_start + i - 1][0])
                    if (move_start + i - 1) < len(moves)
                    else f"{move_start + i}"
                )
                replacements[f"@@WP{i}@@"] = (
                    moves[move_start + i - 1][1] or "" if (move_start + i - 1) < len(moves) else ""
                )
                replacements[f"@@BP{i}@@"] = (
                    moves[move_start + i - 1][2] or "" if (move_start + i - 1) < len(moves) else ""
                )

            button_labels = [" ", " ", " ", " ", " ", " ", " ", " ", " ", " "]

            button_labels[8] = "1/2"
            button_labels[9] = "⚐"
            # Title shows who is to move
            is_player_turn = board.turn == player_color
            replacements["@@TITLE@@"] = (
                f"{player_name} jogando ..."
                if is_player_turn
                else f"Esperando {opponent_name} jogar ..."
            )

            move_list = (game.moves or "").split()
            last_move = None
            if len(move_list) > 0:
                last_move = chess.Move.from_uci(move_list[-1])

            preview = None
            if player_color == board.turn:
                if move_state.step == MoveStateStep.SELECT_PIECE:
                    # Find all legal moves for the player
                    piece_have_move = {piece: False for piece in self.PIECE_TYPE_MAP.values()}
                    # Check piece legal moves
                    for move in board.legal_moves:
                        piece_type = board.piece_type_at(move.from_square)
                        if piece_type in piece_have_move:
                            piece_have_move[piece_type] = True

                    # Add castling explicitly
                    castle_kingside = board.has_kingside_castling_rights(board.turn) and any(
                        board.is_kingside_castling(m) for m in board.legal_moves
                    )

                    castle_queenside = board.has_queenside_castling_rights(board.turn) and any(
                        board.is_queenside_castling(m) for m in board.legal_moves
                    )

                    # Render SVG pieces for each button if there is a valid move
                    for i, (label, piece) in enumerate(self.PIECE_TYPE_MAP.items()):
                        if piece_have_move[piece]:
                            # Render SVG for this piece
                            button_labels[i] = self.PIECE_SVG[player_color][label]
                    # Draw castling buttons if available
                    button_labels[6] = "O-O" if castle_kingside else " "
                    button_labels[7] = "O-O-O" if castle_queenside else " "

                    replacements["@@HELPER_TEXT@@"] = "Selecione a peça para mover"
                elif (
                    move_state.step == MoveStateStep.SELECT_FILE
                    or move_state.step == MoveStateStep.SELECT_RANK
                ):
                    # Get the selected piece from move_state (e.g., 'R' for rook)
                    selected_piece = getattr(move_state, "selected_piece", None)
                    valid_moves = []
                    if selected_piece:
                        selected_piece_type = self.PIECE_TYPE_MAP.get(selected_piece.upper())
                        if selected_piece_type:
                            # Find all legal moves for pieces of this type belonging to the player
                            for move in board.legal_moves:
                                piece = board.piece_at(move.from_square)
                                if (
                                    piece
                                    and piece.piece_type == selected_piece_type
                                    and piece.color == player_color
                                ):
                                    valid_moves.append(move)

                    # Set button labels based on the step
                    if move_state.step == MoveStateStep.SELECT_FILE:
                        # Collect unique files from valid_moves
                        valid_files = []
                        for move in valid_moves:
                            file_index = chess.square_file(move.to_square)
                            if file_index not in valid_files:
                                valid_files.append(file_index)
                        # Fill button_labels with file letters
                        for i in range(8):
                            button_labels[i] = chr(ord("a") + i) if i in valid_files else " "
                        replacements["@@HELPER_TEXT@@"] = "Selecione a coluna de destino"
                    elif move_state.step == MoveStateStep.SELECT_RANK:
                        # Get the selected file
                        selected_file = getattr(move_state, "selected_file", None)
                        if selected_file:
                            file_index = ord(selected_file.lower()) - ord("a")
                            # Filter moves to those with the selected file
                            filtered_moves = [
                                move
                                for move in valid_moves
                                if chess.square_file(move.to_square) == file_index
                            ]
                            # Collect unique ranks from filtered moves
                            valid_ranks = []
                            for move in filtered_moves:
                                rank_index = chess.square_rank(move.to_square)
                                if rank_index not in valid_ranks:
                                    valid_ranks.append(rank_index)
                            # Fill button_labels with rank numbers
                            for i in range(8):
                                button_labels[i] = str(i + 1) if i in valid_ranks else " "
                            replacements["@@HELPER_TEXT@@"] = "Selecione a linha de destino"
                elif move_state.step == MoveStateStep.CONFIRM:
                    replacements["@@HELPER_TEXT@@"] = "Confirmar jogada??"
                    button_labels[0] = "CONFIRMAR"
                    button_labels[7] = "CANCELAR"

                    last_move = chess.Move.from_uci(getattr(move_state, "pending_move", None))
                    preview = san_with_svg(board.san(last_move), player_color, big=True)
                    board.push(last_move)

                # Build a compact move preview based on the current move_state
                sel_piece = getattr(move_state, "selected_piece", None)
                sel_file = getattr(move_state, "selected_file", None)
                sel_rank = getattr(move_state, "selected_rank", None)
                replacements["@@MOVE_TITLE@@"] = "Sua jogada:"
            else:
                replacements["@@TITLE@@"] = f"Esperando {opponent_name} jogar ..."
                replacements["@@HELPER_TEXT@@"] = f"Esperando {opponent_name} jogar ..."
                last_move = board.pop()  # undo last move
                san = board.san(last_move)  # SAN is computed here
                board.push(last_move)
                preview = san_with_svg(str(san), player_color, big=True)
                replacements["@@MOVE_TITLE@@"] = "Sua última jogada:"
                for i in range(10):
                    button_labels[i] = "  "

            if not preview:
                pc = sel_piece if sel_piece else "___"
                fl = sel_file if sel_file else "___"
                # If rank is stored as an int (0-based), convert to 1-based for display
                if isinstance(sel_rank, int):
                    rk = str(sel_rank + 1)
                else:
                    rk = str(sel_rank) if sel_rank else "___"

                # Format as "Piece FileRank" (e.g. "R e4") with placeholders when missing
                preview = f"{self.PIECE_SVG[player_color][pc] if pc in self.PIECE_SVG[player_color] else pc} {fl} {rk}"

            replacements["@@MOVE_PREVIEW@@"] = preview

            board_html = []

            if player_color == chess.WHITE:
                ranks = range(7, -1, -1)
                files = range(0, 8)
            else:
                ranks = range(0, 8)
                files = range(7, -1, -1)

            for i in range(8):
                replacements[f"@@R{i+1}@@"] = str(ranks[i] + 1)
                replacements[f"@@F{i+1}@@"] = chr(ord("a") + files[i])

            last_from = last_move.from_square if last_move else None
            last_to = last_move.to_square if last_move else None

            for rank in ranks:
                for file in files:
                    sq = chess.square(file, rank)
                    piece = board.piece_at(sq)

                    # square color (does NOT change with orientation)
                    color_class = "dark" if (rank + file) % 2 else "light"

                    # last-move highlight
                    extra_class = ""
                    if sq == last_from or sq == last_to:
                        extra_class = " last-move"

                    if piece:
                        cell = (
                            f'<div class="square {color_class}{extra_class}">'
                            f"{self.PIECE_SVG[piece.color][f"{piece.symbol().upper()}"]}"
                            f"</div>"
                        )
                    else:
                        cell = f'<div class="square {color_class}{extra_class}"></div>'

                    board_html.append(cell)

            replacements["@@PIECES@@"] = "\n".join(board_html)

            for i in range(10):
                replacements[f"@@B{i+1}@@"] = button_labels[i]
            try:
                base_png = render_html_file_to_png(
                    "/app/match_screen_2.html",
                    width=self.width,
                    height=self.height,
                    replacements=replacements,
                )
            except Exception:
                return None

            return base_png
        else:
            return None

    def _render_board(
        self,
        draw: ImageDraw.ImageDraw,
        board: chess.Board,
        x: int,
        y: int,
        size: int,
        player_color: chess.Color,
        last_move: Optional[chess.Move] = None,
        pending_move: Optional[chess.Move] = None,
    ) -> None:
        """Render the chess board."""
        square_size = size // 8

        # Determine if board should be flipped
        flip = player_color == chess.BLACK

        for row in range(8):
            for col in range(8):
                # Calculate square coordinates
                if flip:
                    sq_x = x + (7 - col) * square_size
                    sq_y = y + row * square_size
                    square = chess.square(7 - col, 7 - row)
                else:
                    sq_x = x + col * square_size
                    sq_y = y + (7 - row) * square_size
                    square = chess.square(col, row)

                # Square color
                is_light = (row + col) % 2 == 0
                fill = self.WHITE if is_light else self.GRAY

                # Highlight last move
                if last_move and square in [last_move.from_square, last_move.to_square]:
                    fill = (200, 200, 150) if is_light else (150, 150, 100)

                draw.rectangle(
                    [sq_x, sq_y, sq_x + square_size, sq_y + square_size],
                    fill=fill,
                    outline=self.BLACK,
                )

                # Draw piece
                piece = board.piece_at(square)
                if piece:
                    symbol = self.PIECE_SYMBOLS.get(piece.symbol(), "?")
                    # Center the piece in the square
                    text_x = sq_x + square_size // 2 - 8
                    text_y = sq_y + square_size // 2 - 10
                    draw.text((text_x, text_y), symbol, fill=self.BLACK, font=self._font_large)

        # Draw pending move arrow
        if pending_move:
            self._draw_arrow(draw, x, y, square_size, pending_move, flip)

        # Draw file letters (a-h)
        for col in range(8):
            file_letter = chr(ord("a") + (7 - col if flip else col))
            draw.text(
                (x + col * square_size + square_size // 2 - 4, y + size + 2),
                file_letter,
                fill=self.BLACK,
                font=self._font_small,
            )

        # Draw rank numbers (1-8)
        for row in range(8):
            rank_num = str(row + 1 if flip else 8 - row)
            draw.text(
                (x - 12, y + row * square_size + square_size // 2 - 6),
                rank_num,
                fill=self.BLACK,
                font=self._font_small,
            )

    def _draw_arrow(
        self,
        draw: ImageDraw.ImageDraw,
        board_x: int,
        board_y: int,
        square_size: int,
        move: chess.Move,
        flip: bool,
    ) -> None:
        """Draw an arrow indicating a move."""
        from_sq = move.from_square
        to_sq = move.to_square

        from_col, from_row = chess.square_file(from_sq), chess.square_rank(from_sq)
        to_col, to_row = chess.square_file(to_sq), chess.square_rank(to_sq)

        if flip:
            from_col, from_row = 7 - from_col, 7 - from_row
            to_col, to_row = 7 - to_col, 7 - to_row

        from_x = board_x + from_col * square_size + square_size // 2
        from_y = board_y + (7 - from_row) * square_size + square_size // 2
        to_x = board_x + to_col * square_size + square_size // 2
        to_y = board_y + (7 - to_row) * square_size + square_size // 2

        # Draw thick line for arrow
        draw.line([(from_x, from_y), (to_x, to_y)], fill=self.BLACK, width=3)

    def _render_move_state(
        self,
        draw: ImageDraw.ImageDraw,
        move_state: MoveState,
        x: int,
        y: int,
    ) -> None:
        """Render the current move input state."""
        step_text = {
            "select_piece": "Select piece",
            "select_file": f"Select file ({move_state.selected_piece})",
            "select_rank": f"Select rank ({move_state.selected_piece}{move_state.selected_file})",
            "disambiguation": "Choose piece",
            "confirm": f"Confirm: {move_state.pending_move}",
        }

        text = step_text.get(move_state.step.value, "")
        draw.text((x, y), text, fill=self.BLACK, font=self._font_small)

    def _render_button_panel(
        self,
        draw: ImageDraw.ImageDraw,
        button_actions: list[ButtonAction],
    ) -> None:
        """Render the button action panel on the right edge."""
        if not button_actions:
            return

        # Button panel on far right
        panel_x = self.width - 100
        panel_y = 20
        button_height = 35

        for action in button_actions:
            # Draw button indicator
            color = self.BLACK if action.enabled else self.GRAY
            draw.rectangle(
                [panel_x, panel_y, panel_x + 80, panel_y + button_height - 5],
                outline=color,
                width=1,
            )
            draw.text(
                (panel_x + 5, panel_y + 8),
                f"{action.button.value[-1]}: {action.label[:8]}",
                fill=color,
                font=self._font_small,
            )
            panel_y += button_height

    def _image_to_bytes(self, img: Image.Image) -> bytes:
        """Convert PIL Image to PNG bytes."""
        buffer = BytesIO()
        # Convert to grayscale for e-Ink optimization
        img_gray = img.convert("L")
        img_gray.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    @staticmethod
    def compute_hash(image_data: bytes) -> str:
        """Compute SHA256 hash of image data."""
        return hashlib.sha256(image_data).hexdigest()
