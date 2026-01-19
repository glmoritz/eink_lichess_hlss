"""
Renderer service for generating e-Ink display frames.
"""

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Optional

import chess
import chess.svg
import qrcode
from PIL import Image, ImageDraw, ImageFont

from hlss.config import get_settings
from hlss.schemas import ButtonAction, MoveState, ScreenType
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
        selected_user: str,
        selected_color: str,
        button_actions: list[ButtonAction],
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
        replacements = {
            "@@ADVERSARY@@": selected_user,
            "@@PLAYERNAME@@": "Player",
            "@@PLAYERCOLOR@@": selected_color.capitalize(),
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
        move_state: Optional[MoveState],
        button_actions: list[ButtonAction],
        last_move: Optional[chess.Move] = None,
        pending_move: Optional[chess.Move] = None,
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
        img = self._create_base_image()
        draw = ImageDraw.Draw(img)

        self._render_header(draw, "Play")
        self._render_context_bar(draw, button_actions)
        left, top, right, bottom = self._content_bounds()

        # Calculate board dimensions (left side of screen)
        content_height = bottom - top
        content_width = right - left
        board_size = min(content_height, content_width // 2 - 20)
        board_x = left
        board_y = top + (content_height - board_size) // 2

        # Render chess board
        self._render_board(
            draw,
            board,
            board_x,
            board_y,
            board_size,
            player_color,
            last_move,
            pending_move,
        )

        # Right panel starts after the board
        panel_x = board_x + board_size + 20

        # Player names
        y = top
        # Opponent at top (or bottom depending on color)
        draw.text((panel_x, y), f"⚫ {opponent_name}", fill=self.BLACK, font=self._font)
        y += 25

        # Game info
        if board.turn == player_color:
            draw.text((panel_x, y), "Your turn", fill=self.BLACK, font=self._font)
        else:
            draw.text((panel_x, y), "Waiting...", fill=self.GRAY, font=self._font)
        y += 40

        # Move list (last 10 moves)
        moves = list(board.move_stack)[-10:]
        if moves:
            draw.text((panel_x, y), "Recent moves:", fill=self.BLACK, font=self._font_small)
            y += 20
            temp_board = board.copy()
            for _ in range(len(moves)):
                temp_board.pop()
            for i, move in enumerate(moves[-10:]):
                san = temp_board.san(move)
                temp_board.push(move)
                draw.text(
                    (panel_x, y),
                    f"{len(temp_board.move_stack)}. {san}",
                    fill=self.BLACK,
                    font=self._font_small,
                )
                y += 16

        # Player at bottom
        y = bottom - 24
        draw.text((panel_x, y), f"⚪ {player_name}", fill=self.BLACK, font=self._font)

        # Move state indicator
        if move_state:
            self._render_move_state(draw, move_state, panel_x, bottom - 80)

        return self._image_to_bytes(img)

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
