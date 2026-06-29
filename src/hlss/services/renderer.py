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
from hlss.services.move_selection import MoveSelectionHelper
from hlss.services.pil_engine import PilEngine


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

        # Self-contained color-aware 1bpp renderer (replaces HTML/Chrome where ported).
        self.engine = PilEngine(self.width, self.height)

        # Try to load a suitable font
        self._font: Optional[ImageFont.FreeTypeFont] = None
        self._font_small: Optional[ImageFont.FreeTypeFont] = None
        self._font_large: Optional[ImageFont.FreeTypeFont] = None
        self._load_fonts()

    def _use_pil(self) -> bool:
        """Whether to use the self-contained PIL renderer for a ported screen."""
        return self.settings.renderer_backend in ("auto", "pil")

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
        if self._use_pil():
            return self.engine.render_setup_screen(config_url)

        img = self._create_base_image()
        draw = ImageDraw.Draw(img)

        self._render_header(draw, "Configuração")
        self._render_context_bar(draw, [])
        left, top, right, bottom = self._content_bounds()

        instructions = [
            "Nenhuma conta Lichess configurada.",
            "",
            "Escaneie o QR code ou visite:",
            config_url,
            "",
            "para configurar sua conta.",
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
        mode: str,
        card_title: str,
        card_main: str,
        card_sub: str,
        primary_action: str,
        secondary_action: str,
        helper_text: str,
        button_labels: list[str],
    ) -> bytes:
        """
        Render the new match creation screen.

        Args:
            mode: Screen mode (e.g. 'incoming' or 'empty')
            card_title: Title displayed above the card
            card_main: Main card text
            card_sub: Sub card text
            primary_action: Primary action label
            secondary_action: Secondary action label
            helper_text: Helper text below card
            button_labels: List of button labels (B1..B10)

        Returns:
            PNG image data
        """
        if self._use_pil():
            return self.engine.render_new_match_screen(
                mode=mode,
                card_title=card_title,
                card_main=card_main,
                card_sub=card_sub,
                primary_action=primary_action,
                secondary_action=secondary_action,
                helper_text=helper_text,
                button_labels=button_labels,
            )

        replacements = {
            "@@MODE@@": mode,
            "@@CARD_TITLE@@": card_title,
            "@@CARD_MAIN@@": card_main,
            "@@CARD_SUB@@": card_sub,
            "@@PRIMARY_ACTION@@": primary_action,
            "@@SECONDARY_ACTION@@": secondary_action,
            "@@HELPER_TEXT@@": helper_text,
            "@@B1@@": button_labels[0] if len(button_labels) > 0 else " ",
            "@@B2@@": button_labels[1] if len(button_labels) > 1 else " ",
            "@@B3@@": button_labels[2] if len(button_labels) > 2 else " ",
            "@@B4@@": button_labels[3] if len(button_labels) > 3 else " ",
            "@@B5@@": button_labels[4] if len(button_labels) > 4 else " ",
            "@@B6@@": button_labels[5] if len(button_labels) > 5 else " ",
            "@@B7@@": button_labels[6] if len(button_labels) > 6 else " ",
            "@@B8@@": button_labels[7] if len(button_labels) > 7 else " ",
            "@@B9@@": button_labels[8] if len(button_labels) > 8 else " ",
            "@@B10@@": button_labels[9] if len(button_labels) > 9 else " ",
        }
        return render_html_file_to_png(
            "/app/new_match_screen.html",
            width=self.width,
            height=self.height,
            replacements=replacements,
        )

    def render_new_match_pressed_strip(self,
                                        button_labels: list[str]) -> Optional[bytes]:
        """Bottom pressed-strip companion to render_new_match_screen.
        Returns PNG bytes (PIL path only); None when the screen has no
        usable buttons or the legacy HTML renderer is active."""
        if not self._use_pil():
            return None
        try:
            return self.engine.render_new_match_pressed_strip(button_labels)
        except Exception:
            return None

    def render_play_screen_pressed_strip_pil(
        self, game_id: str, player_name: str, db: Session
    ) -> Optional[bytes]:
        """Bottom pressed-strip companion to render_play_screen_pil. Returns
        PNG bytes; None when no usable buttons or view can't be built."""
        from hlss.models import Game

        game = db.get(Game, game_id)
        if not game:
            return None
        view = self._build_play_view(game, player_name, db)
        if view is None:
            return None
        try:
            return self.engine.render_play_pressed_strip(view)
        except Exception:
            return None

    def render_play_screen_enabled_mask_pil(
        self, game_id: str, player_name: str, db: Session
    ) -> Optional[int]:
        """Compute the bottom enabled-slot mask from the PLAY view's button
        list (slot S enabled iff buttons[S] has a non-empty label). Returns
        None when the view can't be built or the legacy HTML path is active."""
        from hlss.models import Game

        if not self._use_pil():
            return None
        game = db.get(Game, game_id)
        if not game:
            return None
        view = self._build_play_view(game, player_name, db)
        if view is None:
            return None
        mask = 0
        for i, tok in enumerate(view.get("buttons", [])[:8]):
            label = tok[0] if isinstance(tok, tuple) and tok else ""
            if label and label.strip():
                mask |= (1 << i)
        return mask

    def render_play_screen_pil(self, game_id: str, player_name: str, db: Session,
                                view_mode: str = "3d") -> Optional[bytes]:
        """Self-contained PIL play-screen renderer (no HTML/Chrome).

        Builds an HTML-agnostic ``view`` dict (plain SAN strings, display-space
        board coords, captured-piece lists, glyph-aware button tokens) and hands
        it to :meth:`PilEngine.render_play`. The dispatch between perspective
        (3d) and top-down (2d) layouts is driven by ``view_mode`` — both share
        the same view dict, only the rasterization differs.
        """
        from hlss.models import Game

        game = db.get(Game, game_id)
        if not game:
            return None
        view = self._build_play_view(game, player_name, db)
        if view is None:
            return None
        view["mode"] = view_mode
        try:
            return self.engine.render_play(view)
        except Exception:
            return None

    def _build_play_view(self, game, player_name: str, db: Session) -> Optional[dict]:
        """Build the HTML-agnostic PLAY ``view`` dict from a Game.

        See :meth:`PilEngine.render_play` for the contract. Split out from
        :meth:`render_play_screen_pil` so alternate renderers/prototypes can
        reuse the exact data-prep.
        """
        from hlss.models import GameStatus

        pos = (
            game.initial_fen
            if (game.initial_fen and game.initial_fen != "startpos")
            else chess.STARTING_FEN
        )
        board = chess.Board(pos)
        player_color = chess.WHITE if game.player_color.value == "white" else chess.BLACK
        adversary_color = not player_color
        player_is_white = player_color == chess.WHITE
        opponent_name = game.opponent_username or "Unknown"

        # ---- orientation helpers (display row 0 = top) ----
        def to_disp(sq: int) -> tuple[int, int]:
            f = chess.square_file(sq)
            r = chess.square_rank(sq)
            if player_color == chess.WHITE:
                return f, 7 - r
            return 7 - f, r

        # ---- move history (plain SAN) + last move ----
        moves: list[tuple[int, Optional[str], Optional[str]]] = []
        current_move_number = board.fullmove_number
        white_play: Optional[str] = None
        black_play: Optional[str] = None
        for uci in (game.moves or "").split():
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                raise ValueError(f"Illegal move {uci} at move {current_move_number}")
            san = board.san(move)
            if board.turn == chess.WHITE:
                white_play = san
            else:
                black_play = san
            board.push(move)
            if board.turn == chess.WHITE:
                moves.append((current_move_number, white_play, black_play))
                current_move_number += 1
                white_play = None
                black_play = None
        if white_play is not None:
            moves.append((current_move_number, white_play, None))

        from hlss.routers.instances import _deserialize_move_state

        move_state = _deserialize_move_state(game.move_state)

        # ---- captured pieces / material advantage ----
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
        captured_count = {chess.WHITE: {}, chess.BLACK: {}}
        for piece_color in (chess.WHITE, chess.BLACK):
            for piece_type, count in initial.items():
                captured_count[piece_color][piece_type] = count - len(
                    board.pieces(piece_type, piece_color)
                )

        def captured_list(color: chess.Color) -> list[tuple[str, bool]]:
            out: list[tuple[str, bool]] = []
            for piece_type in initial:
                n = captured_count[color][piece_type]
                letter = chess.piece_symbol(piece_type).upper()
                out.extend([(letter, color == chess.WHITE)] * max(0, n))
            return out

        player_lost = captured_list(player_color)
        adversary_lost = captured_list(adversary_color)
        player_lost_val = sum(captured_count[player_color][pt] * v for pt, v in values.items())
        adversary_lost_val = sum(
            captured_count[adversary_color][pt] * v for pt, v in values.items()
        )
        advantage = player_lost_val - adversary_lost_val
        # Standard convention: pieces shown next to a player are the ones THAT
        # player captured (the opponent's lost pieces). The "+N" sits with the
        # side that is ahead on material.
        adversary_captured = player_lost  # shown next to adversary name
        user_captured = adversary_lost  # shown next to user name
        adversary_adv = f"(+{advantage})" if advantage > 0 else ""
        user_adv = f"(+{abs(advantage)})" if advantage < 0 else ""

        # ---- view defaults ----
        title = ""
        move_title = ""
        move_preview = ""
        preview_lines: Optional[list[str]] = None
        preview_white = player_is_white
        last_move_sqs: list[tuple[int, int]] = []
        loser_king: Optional[tuple[int, int]] = None
        # buttons: list[10] of (label, white_bool, is_move_bool)
        buttons: list[tuple[str, bool, bool]] = [(" ", True, False)] * 10

        winner = None

        if game.status != GameStatus.STARTED:
            status_map = {
                GameStatus.MATE: "Xeque-mate",
                GameStatus.RESIGN: "Desistência",
                GameStatus.STALEMATE: "Afogamento",
                GameStatus.TIMEOUT: "Esgotamento de tempo",
                GameStatus.DRAW: "Empate",
                GameStatus.OUT_OF_TIME: "Esgotamento de tempo",
                GameStatus.ABORTED: "Jogo Abortado",
                GameStatus.CHEAT: "Cheat detectado",
                GameStatus.NO_START: "Jogo Não iniciado",
                GameStatus.UNKNOWN_FINISH: "Finalização desconhecida",
                GameStatus.VARIANT_END: "Fim de variante",
            }
            reason = status_map.get(game.status, str(game.status.value))

            if game.status == GameStatus.MATE:
                winner = player_name if board.turn != player_color else opponent_name
            elif game.status == GameStatus.RESIGN:
                winner = opponent_name if board.turn == player_color else player_name
            elif game.status in (
                GameStatus.STALEMATE,
                GameStatus.DRAW,
                GameStatus.UNKNOWN_FINISH,
                GameStatus.VARIANT_END,
            ):
                winner = "Empate"
                try:
                    if board.is_stalemate():
                        reason = "Afogamento"
                    elif board.is_insufficient_material():
                        reason = "Material insuficiente"
                    elif (
                        getattr(board, "is_fivefold_repetition", None)
                        and board.is_fivefold_repetition()
                    ):
                        reason = "Repetição (5x)"
                    elif (
                        getattr(board, "can_claim_threefold_repetition", None)
                        and board.can_claim_threefold_repetition()
                    ):
                        reason = "Repetição (3x)"
                    elif getattr(board, "is_seventyfive_moves", None) and board.is_seventyfive_moves():
                        reason = "75 lances sem captura/peão"
                    elif getattr(board, "is_fifty_moves", None) and board.is_fifty_moves():
                        reason = "50 lances sem captura/peão"
                except Exception:
                    pass
            elif game.status == GameStatus.OUT_OF_TIME:
                losing_color = board.turn
                winning_color = not losing_color
                try:
                    if board.is_insufficient_material():
                        winner = "Empate"
                    else:
                        winner = player_name if winning_color == player_color else opponent_name
                except Exception:
                    winner = player_name if winning_color == player_color else opponent_name
            elif game.status == GameStatus.TIMEOUT:
                winner = opponent_name if board.turn == player_color else player_name

            if winner == "Empate":
                summary = f"Empate por {reason}"
            elif winner:
                summary = f"Vencedor: {winner}"
            else:
                summary = f"Encerrado: {reason}"

            title = f"Jogo encerrado: {reason}"
            move_title = "Jogo encerrado"
            preview_lines = [summary, reason] if (winner and winner != "Empate") else [summary]
            buttons = [(" ", True, False)] * 10
            buttons[7] = ("Repetir Jogo", True, False)

            # highlight the losing king
            if winner and winner != "Empate":
                winner_color = player_color if winner == player_name else adversary_color
                loser_color = not winner_color
                king_sq = board.king(loser_color)
                if king_sq is not None:
                    loser_king = to_disp(king_sq)
        else:
            buttons[8] = ("1/2", True, False)
            buttons[9] = ("⚐", True, False)  # flag
            is_player_turn = board.turn == player_color
            title = (
                f"{player_name} jogando ..."
                if is_player_turn
                else f"Esperando {opponent_name} jogar ..."
            )

            move_list = (game.moves or "").split()
            if move_list:
                lm = chess.Move.from_uci(move_list[-1])
                last_move_sqs = [to_disp(lm.from_square), to_disp(lm.to_square)]

            preview = None
            sel_piece = getattr(move_state, "selected_piece", None)
            sel_file = getattr(move_state, "selected_file", None)
            sel_rank = getattr(move_state, "selected_rank", None)

            if is_player_turn:
                step = move_state.step
                if step == MoveStateStep.SELECT_PIECE:
                    piece_have_move = {pt: False for pt in self.PIECE_TYPE_MAP.values()}
                    for mv in board.legal_moves:
                        pt = board.piece_type_at(mv.from_square)
                        if pt in piece_have_move:
                            piece_have_move[pt] = True

                    def piece_suffix(piece_type: int) -> str:
                        mvs = [
                            mv
                            for mv in board.legal_moves
                            if (p := board.piece_at(mv.from_square))
                            and p.piece_type == piece_type
                            and p.color == player_color
                        ]
                        if not mvs:
                            return ""
                        file_indices = {chess.square_file(mv.to_square) for mv in mvs}
                        if piece_type == chess.PAWN:
                            file_indices.update(chess.square_file(mv.from_square) for mv in mvs)
                        if len(file_indices) != 1:
                            return ""
                        fidx = next(iter(file_indices))
                        suffix = chr(ord("a") + fidx)
                        rank_indices = {
                            chess.square_rank(mv.to_square)
                            for mv in mvs
                            if chess.square_file(mv.to_square) == fidx
                        }
                        if piece_type == chess.PAWN:
                            for mv in mvs:
                                if chess.square_file(mv.from_square) == fidx:
                                    rank_indices.add(chess.square_rank(mv.to_square))
                        if len(rank_indices) == 1:
                            suffix += str(next(iter(rank_indices)) + 1)
                        return suffix

                    for i, (letter, pt) in enumerate(self.PIECE_TYPE_MAP.items()):
                        if piece_have_move[pt]:
                            suffix = piece_suffix(pt)
                            buttons[i] = (f"{letter}{suffix}" if suffix else letter,
                                          player_is_white, True)
                    castle_kingside = board.has_kingside_castling_rights(board.turn) and any(
                        board.is_kingside_castling(m) for m in board.legal_moves
                    )
                    castle_queenside = board.has_queenside_castling_rights(board.turn) and any(
                        board.is_queenside_castling(m) for m in board.legal_moves
                    )
                    buttons[6] = ("O-O", player_is_white, True) if castle_kingside else (" ", True, False)
                    buttons[7] = ("O-O-O", player_is_white, True) if castle_queenside else (" ", True, False)
                elif step in (MoveStateStep.SELECT_FILE, MoveStateStep.SELECT_RANK):
                    selected_piece = sel_piece
                    valid_moves: list[chess.Move] = []
                    selected_piece_type = (
                        self.PIECE_TYPE_MAP.get(selected_piece.upper()) if selected_piece else None
                    )
                    if selected_piece and selected_piece_type:
                        valid_moves = MoveSelectionHelper.get_piece_moves(board, selected_piece)

                    if step == MoveStateStep.SELECT_FILE:
                        file_rank_map: dict[int, set[int]] = {}
                        for mv in valid_moves:
                            to_file = chess.square_file(mv.to_square)
                            file_rank_map.setdefault(to_file, set()).add(
                                chess.square_rank(mv.to_square)
                            )
                            if selected_piece_type == chess.PAWN:
                                from_file = chess.square_file(mv.from_square)
                                file_rank_map.setdefault(from_file, set()).add(
                                    chess.square_rank(mv.to_square)
                                )
                        for i in range(8):
                            if i in file_rank_map:
                                label = chr(ord("a") + i)
                                ranks_s = file_rank_map[i]
                                if len(ranks_s) == 1:
                                    label += str(next(iter(ranks_s)) + 1)
                                buttons[i] = (label, True, False)
                            else:
                                buttons[i] = (" ", True, False)
                    else:  # SELECT_RANK
                        if sel_file:
                            file_index = ord(sel_file.lower()) - ord("a")
                            filtered = [
                                mv for mv in valid_moves
                                if chess.square_file(mv.to_square) == file_index
                            ]
                            filtered.extend(
                                mv for mv in valid_moves
                                if chess.square_file(mv.from_square) == file_index
                                and board.piece_at(mv.from_square)
                                and board.piece_at(mv.from_square).piece_type == chess.PAWN
                            )
                            valid_ranks = []
                            for mv in filtered:
                                ri = chess.square_rank(mv.to_square)
                                if ri not in valid_ranks:
                                    valid_ranks.append(ri)
                            for i in range(8):
                                buttons[i] = (str(i + 1), True, False) if i in valid_ranks else (" ", True, False)
                elif step == MoveStateStep.DISAMBIGUATION:
                    for i, move_uci in enumerate(
                        getattr(move_state, "disambiguation_options", [])
                    ):
                        if i < 10:
                            mv = chess.Move.from_uci(move_uci)
                            buttons[i] = (board.san(mv), player_is_white, True)
                elif step == MoveStateStep.CONFIRM:
                    buttons[0] = ("CONFIRMAR", True, False)
                    buttons[7] = ("CANCELAR", True, False)
                    pend = chess.Move.from_uci(getattr(move_state, "pending_move", None))
                    preview = board.san(pend)
                    preview_white = player_is_white
                    last_move_sqs = [to_disp(pend.from_square), to_disp(pend.to_square)]
                    board.push(pend)

                move_title = "Sua jogada:"
                if preview is None:
                    pc = sel_piece if sel_piece else "___"
                    fl = sel_file if sel_file else "___"
                    if isinstance(sel_rank, int):
                        rk = str(sel_rank + 1)
                    else:
                        rk = str(sel_rank) if sel_rank else "___"
                    preview = f"{pc} {fl} {rk}"
                    preview_white = player_is_white
                move_preview = preview
            else:
                title = f"Esperando {opponent_name} jogar ..."
                move_title = "Sua última jogada:"
                if move_list:
                    lm = board.pop()
                    move_preview = board.san(lm)
                    board.push(lm)
                    preview_white = player_is_white
                buttons[:8] = [(" ", True, False)] * 8

        # ---- board squares (after any CONFIRM preview push) ----
        squares: list[tuple[int, int, Optional[str], bool]] = []
        rank_labels: list[int] = []
        file_labels: list[str] = []
        for dr in range(8):
            for dc in range(8):
                if player_color == chess.WHITE:
                    rank = 7 - dr
                    file = dc
                else:
                    rank = dr
                    file = 7 - dc
                sq = chess.square(file, rank)
                piece = board.piece_at(sq)
                dark = bool((rank + file) % 2)
                symbol = piece.symbol() if piece else None
                squares.append((dc, dr, symbol, dark))
        for dr in range(8):
            rank = (7 - dr) if player_color == chess.WHITE else dr
            rank_labels.append(rank + 1)
        for dc in range(8):
            file = dc if player_color == chess.WHITE else (7 - dc)
            file_labels.append(chr(ord("a") + file))

        last_move_san: Optional[str] = None
        last_move_white: bool = False
        for _num, _w, _b in reversed(moves):
            if _b:
                last_move_san = _b
                last_move_white = False
                break
            if _w:
                last_move_san = _w
                last_move_white = True
                break

        view = {
            "title": title,
            "orientation_white": player_is_white,
            "squares": squares,
            "last_move": last_move_sqs,
            "last_move_san": last_move_san,
            "last_move_white": last_move_white,
            "loser_king": loser_king,
            "rank_labels": rank_labels,
            "file_labels": file_labels,
            "user_name": player_name,
            "adversary_name": opponent_name,
            "user_captured": user_captured,
            "adversary_captured": adversary_captured,
            "user_adv": user_adv,
            "adversary_adv": adversary_adv,
            "moves": moves,
            "move_title": move_title,
            "move_preview": move_preview,
            "preview_lines": preview_lines,
            "preview_white": preview_white,
            "buttons": buttons,
        }
        return view

    def render_play_screen(self, game_id: str, player_name: str, db: Session,
                            view_mode: str = "3d") -> bytes:
        """
        Render the game play screen.

        Args:
            game_id: The id of the game to render
            player_name: Player's username
            db: Database session
            view_mode: "3d" (default) or "2d" — selects the board layout
                (PIL path only; the legacy HTML path ignores it).

        Returns:
            PNG image data
        """
        if self._use_pil():
            return self.render_play_screen_pil(game_id, player_name, db,
                                               view_mode=view_mode)

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
            last_move = None

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
            winner = None

            # If the game is not started, show end reason and demand new game option
            from hlss.models import GameStatus

            if game.status != GameStatus.STARTED:
                # Map status to human-readable reason
                status_map = {
                    GameStatus.MATE: "Xeque-mate",
                    GameStatus.RESIGN: "Desistência",
                    GameStatus.STALEMATE: "Afogamento",
                    GameStatus.TIMEOUT: "Esgotamento de tempo",
                    GameStatus.DRAW: "Empate",
                    GameStatus.OUT_OF_TIME: "Esgotamento de tempo",
                    GameStatus.ABORTED: "- Jogo Abortado",
                    GameStatus.CHEAT: "- Cheat detectado",
                    GameStatus.NO_START: "- Jogo Não iniciado",
                    GameStatus.UNKNOWN_FINISH: "Finalização desconhecida",
                    GameStatus.VARIANT_END: "Fim de variante",
                }
                reason = status_map.get(game.status, str(game.status.value))

                # Determine winner (human-readable) and compose winner_text
                if game.status == GameStatus.MATE:
                    winner = player_name if board.turn != player_color else opponent_name
                elif game.status == GameStatus.RESIGN:
                    winner = opponent_name if board.turn == player_color else player_name
                elif game.status in [
                    GameStatus.STALEMATE,
                    GameStatus.DRAW,
                    GameStatus.UNKNOWN_FINISH,
                    GameStatus.VARIANT_END,
                ]:
                    # Many kinds of draws/variant ends — try to detect the specific reason
                    # using python-chess helper methods (repetition, 75-move, insufficient material, etc.).
                    winner = "Empate"
                    # Prefer board-based detection when available
                    try:
                        if board.is_stalemate():
                            reason = "Afogamento"
                        elif board.is_insufficient_material():
                            reason = "Material insuficiente"
                        elif (
                            getattr(board, "is_fivefold_repetition", None)
                            and board.is_fivefold_repetition()
                        ):
                            reason = "Repetição (5x)"
                        elif (
                            getattr(board, "can_claim_threefold_repetition", None)
                            and board.can_claim_threefold_repetition()
                        ):
                            reason = "Repetição (3x)"
                        elif (
                            getattr(board, "is_seventyfive_moves", None)
                            and board.is_seventyfive_moves()
                        ):
                            reason = "75 movimentos sem avanço de peões"
                        elif getattr(board, "is_fifty_moves", None) and board.is_fifty_moves():
                            reason = "50 movimentos sem avanço de peões"
                        else:
                            # keep the previously derived reason from status_map
                            pass
                    except Exception:
                        # If any detection fails, fall back to generic mapping
                        pass
                elif game.status == GameStatus.OUT_OF_TIME:
                    # A player ran out of time. They lose unless the opponent
                    # has insufficient mating material, in which case it's a draw.
                    # The side to move in the final position is the side that
                    # ran out of time.
                    losing_color = board.turn
                    winning_color = not losing_color
                    try:
                        # python-chess provides a board-level insufficient-material
                        # detection. If it returns True the position is drawn by
                        # insufficient material (no side can mate).
                        if board.is_insufficient_material():
                            winner = "Empate"
                        else:
                            winner = player_name if winning_color == player_color else opponent_name
                    except Exception:
                        # On any failure, conservatively treat as a loss for the
                        # player who ran out of time.
                        winner = player_name if winning_color == player_color else opponent_name
                elif game.status == GameStatus.TIMEOUT:
                    # Timeout here indicates connection forfeiture. Treat as a
                    # loss for the player who disconnected (no insufficient-
                    # material check).
                    winner = opponent_name if board.turn == player_color else player_name
                else:
                    winner = None

                if winner == "Empate":
                    winner_text = "Empate"
                elif winner:
                    winner_text = f"Vencedor: {winner}"
                else:
                    winner_text = ""

                # Update only the end-of-game specific replacements so we don't lose
                # previously computed values (captured pieces, move list, etc.).
                end_repl = {
                    "@@TITLE@@": f"Jogo encerrado: {reason}",
                    "@@HELPER_TEXT@@": "Pressione ENTER para desafiar novamente",
                    "@@MOVE_TITLE@@": "Jogo encerrado",
                    "@@MOVE_PREVIEW@@": (
                        f"{winner_text} ganhou por {reason}"
                        if winner and winner != "Empate"
                        else winner_text or f"Encerrado: {reason}"
                    ),
                    "@@ADVERSARY@@": game.opponent_username or "Unknown",
                    "@@USER@@": player_name,
                }
                replacements.update(end_repl)

                # Clear most buttons and offer a repeat challenge on B8 (index 7)
                button_labels = [" "] * 10
                button_labels[7] = "Repetir Jogo"
            else:
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

                        def piece_suffix(piece_type: int) -> str:
                            moves = []
                            for mv in board.legal_moves:
                                piece = board.piece_at(mv.from_square)
                                if (
                                    piece
                                    and piece.piece_type == piece_type
                                    and piece.color == player_color
                                ):
                                    moves.append(mv)
                            if not moves:
                                return ""

                            file_indices = {chess.square_file(mv.to_square) for mv in moves}
                            if piece_type == chess.PAWN:
                                file_indices.update(
                                    chess.square_file(mv.from_square) for mv in moves
                                )

                            if len(file_indices) != 1:
                                return ""

                            file_index = next(iter(file_indices))
                            suffix = chr(ord("a") + file_index)

                            rank_indices = {
                                chess.square_rank(mv.to_square)
                                for mv in moves
                                if chess.square_file(mv.to_square) == file_index
                            }
                            if piece_type == chess.PAWN:
                                for mv in moves:
                                    if chess.square_file(mv.from_square) == file_index:
                                        rank_indices.add(chess.square_rank(mv.to_square))

                            if len(rank_indices) == 1:
                                suffix += str(next(iter(rank_indices)) + 1)

                            return suffix

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
                                suffix = piece_suffix(piece)
                                base = self.PIECE_SVG[player_color][label]
                                button_labels[i] = f"{base}{suffix}" if suffix else base
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
                            selected_piece_type = (
                                self.PIECE_TYPE_MAP.get(selected_piece.upper())
                                if selected_piece
                                else None
                            )
                            valid_moves: list[chess.Move] = []
                            if selected_piece and selected_piece_type:
                                valid_moves = MoveSelectionHelper.get_piece_moves(
                                    board, selected_piece
                                )

                        # Set button labels based on the step
                        if move_state.step == MoveStateStep.SELECT_FILE:
                            file_rank_map: dict[int, set[int]] = {}
                            for move in valid_moves:
                                to_file = chess.square_file(move.to_square)
                                file_rank_map.setdefault(to_file, set()).add(
                                    chess.square_rank(move.to_square)
                                )
                                if selected_piece_type == chess.PAWN:
                                    from_file = chess.square_file(move.from_square)
                                    file_rank_map.setdefault(from_file, set()).add(
                                        chess.square_rank(move.to_square)
                                    )

                            # Fill button_labels with file letters (append rank if unique)
                            for i in range(8):
                                if i in file_rank_map:
                                    label = chr(ord("a") + i)
                                    ranks = file_rank_map[i]
                                    if len(ranks) == 1:
                                        label += str(next(iter(ranks)) + 1)
                                    button_labels[i] = label
                                else:
                                    button_labels[i] = " "
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

                                pawn_captures = [
                                    move
                                    for move in valid_moves
                                    if chess.square_file(move.from_square) == file_index
                                    and board.piece_at(move.from_square).piece_type == chess.PAWN
                                ]

                                filtered_moves.extend(pawn_captures)

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
                    elif move_state.step == MoveStateStep.DISAMBIGUATION:
                        replacements["@@HELPER_TEXT@@"] = "Escolha a jogada desejada"
                        for i, move_uci in enumerate(
                            getattr(move_state, "disambiguation_options", [])
                        ):
                            if i < 10:
                                move = chess.Move.from_uci(move_uci)
                                san = board.san(move)
                                button_labels[i] = san_with_svg(san, player_color, big=True)
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

            # now draw the board squares
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

            for rank in ranks:
                for file in files:
                    sq = chess.square(file, rank)
                    piece = board.piece_at(sq)

                    # square color (does NOT change with orientation)
                    color_class = "dark" if (rank + file) % 2 else "light"

                    # last-move highlight
                    extra_class = ""

                    if last_move is not None:
                        if sq == last_move.from_square or sq == last_move.to_square:
                            extra_class = " last-move"

                    if piece:
                        # Highlight the king of the losing side if there is a winner
                        highlight_loser_king = False
                        if winner is not None and winner != "Empate":
                            # Determine the color of the losing side
                            winner_color = (
                                player_color if winner == player_name else adversary_color
                            )
                            loser_color = not winner_color
                            if piece.piece_type == chess.KING and piece.color == loser_color:
                                highlight_loser_king = True
                        if highlight_loser_king:
                            extra_class += " flipped"

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

    @staticmethod
    def compute_hash(image_data: bytes) -> str:
        """Compute SHA256 hash of image data."""
        return hashlib.sha256(image_data).hexdigest()
