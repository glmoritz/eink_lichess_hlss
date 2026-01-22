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

    def __init__(self):
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

    def render_play_screen(
        self,
        board: chess.Board,
        player_color: chess.Color,
        opponent_name: str,
        player_name: str,
        move_state: MoveState,
        pending_move: Optional[str] = None,
    ) -> bytes:
        """
        Render the game play screen.

        Args:
            board: Current chess board state
            player_color: Which color the player is
            opponent_name: Opponent's username
            player_name: Player's username
            move_state: Current move input state
            button_actions: List of button action mappings
            last_move: Last move made (for highlighting)
            pending_move: Move being constructed (for arrow)

        Returns:
            PNG image data
        """
        # Render the static HTML base first, then overlay the SVG board.
        # Prepare simple replacements for the HTML template.

        # SELECT_PIECE = "select_piece"
        #     SELECT_FILE = "select_file"
        #     SELECT_RANK = "select_rank"
        #     DISAMBIGUATION = "disambiguation"
        #     CONFIRM = "confirm"

        replacements = {
            "@@TITLE@@": "Play",
            "@@ADVERSARY@@": opponent_name,
            "@@ADVERSARY_CAPTURED@@": "",
            "@@USER@@": player_name,
            "@@USER_CAPTURED@@": "",
            "@@MOVE_PREVIEW@@": move_preview,
        }

        # calcula o número de peças capturadas
        initial = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 1}

        values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}

        # Helper to build captured pieces string
        def build_captured_str(captured_dict, color):
            symbols = []
            for piece_type in captured_dict.keys():
                count = captured_dict[piece_type]
                if count > 0:
                    symbol = self.PIECE_SYMBOLS[chess.Piece(piece_type, color).symbol()]
                    if piece_type == chess.PAWN and count > 2:
                        symbols.append(f"{count}{symbol}")
                    else:
                        symbols.extend([symbol] * count)
            return " ".join(symbols)

        captured = {chess.PAWN: 0, chess.KNIGHT: 0, chess.BISHOP: 0, chess.ROOK: 0, chess.QUEEN: 0}
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

        replacements["@@USER_CAPTURED@@"] = user_captured_str
        replacements["@@ADVERSARY_CAPTURED@@"] = adversary_captured_str

        # Prepare last 8 moves for the move list
        move_stack = list(board.move_stack)
        num_moves = board.fullmove_number

        # Helper to replace piece letter with unicode symbol
        def san_with_unicode(san: str) -> str:
            for piece, symbol in self.PIECE_SYMBOLS.items():
                san = san.replace(piece.upper(), symbol).replace(piece.lower(), symbol)
            return san

        for i in range(8):
            move_index = num_moves - 8 + i
            n_key = f"@@N{i+1}@@"
            wp_key = f"@@WP{i+1}@@"
            bp_key = f"@@BP{i+1}@@"
            ply_num = (move_index // 2) + 1 if move_index >= 0 else ""
            w_move = ""
            b_move = ""
            if move_index >= 0:
                if move_index % 2 == 0:
                    # White move
                    try:
                        w_move = san_with_unicode(board.san(move_stack[move_index]))
                    except Exception:
                        w_move = str(move_stack[move_index])
                    # Black move may exist
                    if move_index + 1 < num_moves:
                        try:
                            b_move = san_with_unicode(board.san(move_stack[move_index + 1]))
                        except Exception:
                            b_move = str(move_stack[move_index + 1])
                else:
                    # Odd index: black move, white move is empty
                    try:
                        b_move = san_with_unicode(board.san(move_stack[move_index]))
                    except Exception:
                        b_move = str(move_stack[move_index])
            replacements[n_key] = str(ply_num)
            replacements[wp_key] = w_move
            replacements[bp_key] = b_move

        button_labels = [" ", " ", " ", " ", " ", " ", " ", " ", " ", " "]

        if move_state.step == MoveStateStep.SELECT_PIECE:
            button_labels[8] = "Empatar"
            button_labels[9] = "Abandonar"
            # Title shows who is to move
            is_player_turn = board.turn == player_color
            replacements["@@TITLE@@"] = (
                f"{player_name} a jogar" if is_player_turn else f"{opponent_name} a jogar"
            )
            replacements["@@HELPER_TEXT@@"] = "Selecione a peça para mover"
            replacements["@@MOVE_PREVIEW@@"] = "____   ____   ____"

        try:
            base_png = render_html_file_to_png(
                "/app/match_screen.html",
                width=self.width,
                height=self.height,
                replacements=replacements,
            )
        except Exception:
            return None

        # Open base image from HTML renderer
        base_img = Image.open(BytesIO(base_png)).convert("RGBA")

        # Compute board placement to match the HTML layout.
        # HTML uses left column 400px, board width/height 320px and centered inside that column.
        board_size = 320
        board_x = 42  # (400 - 320) / 2
        board_y = 61

        # Render the chess board to SVG and convert to PNG via cairosvg
        try:

            svg = chess.svg.board(
                board=board,
                size=board_size,
                lastmove=last_move,
                orientation=(chess.WHITE if player_color == chess.WHITE else chess.BLACK),
            )
            board_png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
            board_img = Image.open(BytesIO(board_png)).convert("RGBA")
            # Paste board onto base image
            base_img.paste(board_img, (board_x, board_y), board_img)
        except Exception:
            pass

        if move_state.step == MoveStateStep.SELECT_PIECE:
            # Board and move state context
            button_height = self.FOOTER_HEIGHT
            button_width = self.width // 8
            bar_top = self.height - button_height
            bar_center_y = bar_top + button_height // 2

            # Map button index to piece type for standard moves
            piece_buttons = [
                chess.PAWN,
                chess.KNIGHT,
                chess.BISHOP,
                chess.ROOK,
                chess.QUEEN,
                chess.KING,
            ]

            # Find all legal moves for the player
            legal_moves = list(board.legal_moves)
            from_square = move_state.selected_square
            piece_moves = {pt: False for pt in piece_buttons}
            castle_kingside = False
            castle_queenside = False

            for move in legal_moves:
                if from_square is not None and move.from_square == from_square:
                    piece = board.piece_at(move.from_square)
                    if piece:
                        if piece.piece_type in piece_moves:
                            piece_moves[piece.piece_type] = True
                    if board.is_kingside_castling(move):
                        castle_kingside = True
                    if board.is_queenside_castling(move):
                        castle_queenside = True

            # Render SVG pieces for each button if there is a valid move
            for i in range(8):
                btn_x = i * button_width
                center_x = btn_x + button_width // 2
                if i < 6:
                    piece_type = piece_buttons[i]
                    if piece_moves[piece_type]:
                        # Render SVG for this piece
                        piece = chess.Piece(piece_type, player_color)
                        svg = chess.svg.piece(piece, size=32)
                        png_bytes = cairosvg.svg2png(
                            bytestring=svg.encode("utf-8"), output_width=32, output_height=32
                        )
                        piece_img = Image.open(BytesIO(png_bytes)).convert("RGBA")
                        img_x = center_x - piece_img.width // 2
                        img_y = bar_center_y - piece_img.height // 2
                        base_img.paste(piece_img, (img_x, img_y), piece_img)
                elif i == 6 and castle_kingside:
                    # Draw O-O for kingside castle
                    text = "O-O"
                    draw = ImageDraw.Draw(base_img)
                    w, h = draw.textsize(text, font=self._font_large)
                    draw.text(
                        (center_x - w // 2, bar_center_y - h // 2),
                        text,
                        fill=self.BLACK,
                        font=self._font_large,
                    )
                elif i == 7 and castle_queenside:
                    # Draw O-O-O for queenside castle
                    text = "O-O-O"
                    draw = ImageDraw.Draw(base_img)
                    w, h = draw.textsize(text, font=self._font_large)
                    draw.text(
                        (center_x - w // 2, bar_center_y - h // 2),
                        text,
                        fill=self.BLACK,
                        font=self._font_large,
                    )

        # Compute button centers on a transparent layer (so we don't redraw buttons over the HTML)
        temp_img = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        temp_draw = ImageDraw.Draw(temp_img)
        centers = self._render_context_bar(temp_draw, button_actions)

        # Return final PNG bytes
        # Convert back to RGB for consistency with other renderers
        final_img = base_img.convert("RGB")
        return self._image_to_bytes(final_img)

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
