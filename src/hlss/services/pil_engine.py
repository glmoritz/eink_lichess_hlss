"""
Self-contained, color-aware 1bpp renderer (no HTML, no headless Chrome).

Design goals (for a 1bpp mono e-ink panel):
- Output is **pure black/white** (only 0 and 255). The downstream LLSS converter
  Floyd-Steinberg dithers, but FS is a no-op on already-binary pixels, so a pure
  B/W frame survives untouched -> crisp.
- Grays (chess dark squares, panel fills) are rendered as **ordered dot-mesh**
  (Bayer 8x8) directly in B/W, never as flat gray that a threshold would crush.
- Text and chess pieces are drawn normally on an L (8-bit) canvas and the WHOLE
  canvas is **thresholded once at the end** -> text edges become crisp B/W (no AA
  gray fringe) and the dot-mesh regions (already 0/255) pass through unchanged.
  This is why small fonts and pieces stay sharp: nothing is ever dithered.

Pieces come from python-chess SVGs rasterized once via cairosvg and cached.

Keep output a single-channel PNG; LLSS turns it into the packed 1bpp framebuffer.
"""

from __future__ import annotations

import functools
import io
from pathlib import Path
from typing import Optional

import cairosvg
import chess
import chess.svg
from PIL import Image, ImageDraw, ImageFont

WHITE = 255
BLACK = 0

# Standard Bayer 8x8 ordered-dither matrix (values 0..63).
_BAYER8 = [
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
]
# Per-cell threshold in 0..255 (a pixel of value v is white where v > threshold).
_BAYER_THRESH = [[(c + 0.5) / 64.0 * 255.0 for c in row] for row in _BAYER8]

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

# ---- PLAY screen: Chess Player 2150 sprites + classic-Mac fonts ----
_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "cp2150"
_CHICAGO = _ASSET_DIR / "ChicagoFLF.ttf"
# Geneva as PIL bitmap fonts (.pil/.pbm) extracted from the original Mac bitmap
# strikes. PIL renders these itself (no FreeType), so they're portable — the
# original .dfont relied on Apple-bitmap FreeType support absent in slim images.
_GENEVA15 = _ASSET_DIR / "Geneva_15.pil"
_GENEVA12 = _ASSET_DIR / "Geneva_12.pil"
# python-chess symbol (upper=white / lower=black) -> sprite filename.
# The *2 set is bg-removed + trimmed (clean alpha, uniform ~48x76 footprint).
_SYM2FILE = {
    "P": "white_pawn2", "N": "white_knight2", "B": "white_bishop2",
    "R": "white_rook2", "Q": "white_queen2", "K": "white_king2",
    "p": "black_pawn2", "n": "black_knight2", "b": "black_bishop2",
    "r": "black_rook2", "q": "black_queen2", "k": "black_king2",
}
# 2D pixel-art piece glyphs (eink_chess): black pieces are solid silhouettes,
# white pieces are outlined — used for captured/opponent (and later button/
# last-move/selector) glyphs. Small (~20-33px native), NEAREST-scaled.
_GLYPH2D_DIR = _ASSET_DIR.parent / "glyphs2d"
_PIECE2NAME = {"K": "king", "Q": "queen", "R": "rook",
               "B": "bishop", "N": "knight", "P": "pawn"}
# captured-piece Unicode glyph offsets (white base U+2654, black base U+265A)
_GORD = {"K": 0, "Q": 1, "R": 2, "B": 3, "N": 4, "P": 5}


class PilEngine:
    """Color-aware 1bpp primitives + screen renderers."""

    HEADER_H = 50
    FOOTER_H = 50
    MARGIN = 10

    def __init__(self, width: int = 800, height: int = 480):
        self.width = width
        self.height = height
        self._load_fonts()
        self._cp_cache: dict[str, tuple] = {}
        self._init_board_geometry()

    # ---- fonts ----------------------------------------------------------
    def _pick_font(self, paths: list[str], size: int) -> ImageFont.FreeTypeFont:
        for p in paths:
            if Path(p).exists():
                try:
                    # PIL bitmap fonts (.pil + sibling .pbm) load via load(),
                    # render without FreeType, and are fixed-size (size ignored).
                    if str(p).endswith(".pil"):
                        return ImageFont.load(str(p))
                    return ImageFont.truetype(p, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def _load_fonts(self) -> None:
        self.f_tiny = self._pick_font(_FONT_PATHS, 12)
        self.f_small = self._pick_font(_FONT_PATHS, 15)
        self.f = self._pick_font(_FONT_PATHS, 18)
        self.f_large = self._pick_font(_FONT_BOLD_PATHS, 26)
        self.f_huge = self._pick_font(_FONT_BOLD_PATHS, 34)
        self.f_label = self._pick_font(_FONT_BOLD_PATHS, 14)
        # classic-Mac fonts for the PLAY screen (fall back to DejaVu if missing)
        self.f_mac_title = self._pick_font([str(_CHICAGO)] + _FONT_BOLD_PATHS, 20)
        self.f_mac = self._pick_font([str(_GENEVA15)] + _FONT_PATHS, 15)
        self.f_mac_small = self._pick_font([str(_GENEVA12)] + _FONT_PATHS, 12)
        self.f_glyph = self._pick_font(_FONT_PATHS, 16)   # DejaVu has chess glyphs
        self._g2d_cache: dict = {}                        # 2D piece-sprite cache

    # ---- canvas / finalize ---------------------------------------------
    def _canvas(self) -> Image.Image:
        """White 8-bit (L) working canvas."""
        return Image.new("L", (self.width, self.height), WHITE)

    @staticmethod
    def finalize(img: Image.Image) -> bytes:
        """Threshold the L canvas to pure B/W and return PNG bytes.

        Threshold at 128: dot-mesh regions are already 0/255 (unchanged); AA text
        and piece edges snap to the nearest of black/white -> crisp, no gray.
        """
        bw = img.point(lambda v: 255 if v >= 128 else 0, mode="L")
        buf = io.BytesIO()
        # mode "1" keeps the PNG tiny and unambiguously binary.
        bw.convert("1").save(buf, format="PNG")
        return buf.getvalue()

    # ---- ordered dot-mesh grays ----------------------------------------
    @functools.lru_cache(maxsize=16)
    def _mesh_field(self, level: int) -> Image.Image:
        """Full-canvas L image filled with a Bayer dot-mesh approximating `level`.

        level: 0 (solid black) .. 255 (solid white). Cached per level.
        """
        w, h = self.width, self.height
        data = bytearray(w * h)
        thr = _BAYER_THRESH
        i = 0
        for y in range(h):
            row = thr[y & 7]
            for x in range(w):
                data[i] = WHITE if level > row[x & 7] else BLACK
                i += 1
        field = Image.frombytes("L", (w, h), bytes(data))
        return field

    def mesh_fill(self, img: Image.Image, box: tuple[int, int, int, int], level: int) -> None:
        """Fill `box` (x0,y0,x1,y1) of `img` with a dot-mesh of the given gray level.

        The mesh is aligned to the global pixel grid so adjacent fills tile
        seamlessly. level 255 == leave white (skip).
        """
        if level >= 255:
            return
        x0, y0, x1, y1 = box
        x0i, y0i, x1i, y1i = int(x0), int(y0), int(x1), int(y1)
        if x1i <= x0i or y1i <= y0i:
            return
        field = self._mesh_field(level)
        img.paste(field.crop((x0i, y0i, x1i, y1i)), (x0i, y0i))

    # ---- chess piece bitmaps (cairosvg, cached) ------------------------
    @functools.lru_cache(maxsize=64)
    def _piece_rgba(self, symbol: str, size: int) -> Image.Image:
        """Rasterize a piece SVG to an RGBA PIL image of `size` px (cached).

        `symbol` is python-chess piece symbol: uppercase=white, lowercase=black.
        Standard chess.svg pieces are black-outlined with white fill, so they
        read on both white and dot-mesh-dark squares.
        """
        piece = chess.Piece.from_symbol(symbol)
        svg = chess.svg.piece(piece)
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                               output_width=size, output_height=size)
        return Image.open(io.BytesIO(png)).convert("RGBA")

    def paste_piece(self, img: Image.Image, symbol: str,
                    box: tuple[int, int, int, int], scale: float = 1.0) -> None:
        """Center a piece glyph in `box` on the L canvas, composited via alpha.

        Transparent areas let the underlying square (mesh or white) show through;
        the piece's white fill occludes the mesh so the glyph stays legible.
        """
        x0, y0, x1, y1 = box
        side = int(min(x1 - x0, y1 - y0) * scale)
        if side <= 0:
            return
        rgba = self._piece_rgba(symbol, side)
        # Convert piece luminance to L; use alpha as paste mask.
        gray = rgba.convert("L")
        alpha = rgba.split()[3]
        px = int(x0 + ((x1 - x0) - side) / 2)
        py = int(y0 + ((y1 - y0) - side) / 2)
        img.paste(gray, (px, py), alpha)

    # ---- 2D pixel-art piece glyphs (captured / labels) -----------------
    def _glyph2d_img(self, letter: str, white: bool, target_h: Optional[int] = None):
        """Return the 2D sprite for a piece (cached). `target_h=None` keeps the
        sprite at its NATIVE size — these B&W pixel-art glyphs are designed for
        one size and resizing ruins them, so native is the default."""
        key = (letter.upper(), bool(white), int(target_h) if target_h else 0)
        cached = self._g2d_cache.get(key)
        if cached is not None:
            return cached
        name = _PIECE2NAME.get(letter.upper())
        if not name:
            return None
        path = _GLYPH2D_DIR / f"2d{'white' if white else 'black'}_{name}.png"
        if not path.exists():
            return None
        im = Image.open(path).convert("RGBA")
        if target_h and im.height != target_h and im.height > 0:
            w = max(1, round(im.width * target_h / im.height))
            im = im.resize((w, target_h), Image.NEAREST)
        self._g2d_cache[key] = im
        return im

    def paste_glyph2d(self, img: Image.Image, letter: str, white: bool,
                      x: int, y: int, target_h: Optional[int] = None) -> int:
        """Paste a 2D piece sprite with its top-left at (x, y), scaled to
        target_h, alpha-composited on the L canvas. Returns advance width."""
        im = self._glyph2d_img(letter, white, target_h)
        if im is None:
            return 0
        img.paste(im.convert("L"), (int(x), int(y)), im.split()[3])
        return im.width

    def _draw_captured(self, draw: ImageDraw.ImageDraw, x: int, y: int,
                       captured: list, adv: str = "") -> None:
        """Row of captured pieces as Unicode glyphs. Equal pieces are stacked
        with a small overlap to stay compact; per type at most a few are shown,
        and the material '+N' advantage is appended. Empty -> dash."""
        if not captured:
            self.text(draw, (x, y + 2), "—", self.f_mac_small)
            return
        from collections import Counter
        value = {"Q": 0, "R": 1, "B": 2, "N": 3, "P": 4}
        counts = Counter(letter for (letter, _w) in captured)
        white = captured[0][1]
        f = self.f_glyph
        step = 6                                  # overlap step for equal pieces
        gw = max(1, self.text_w(draw, self._cap_glyph("Q", white), f))
        cx, limit = x, x + 150
        for letter in sorted(counts, key=lambda l: value.get(l, 9)):
            n = counts[letter]
            sym = self._cap_glyph(letter, white)
            shown = min(n, 5)
            for i in range(shown):
                draw.text((cx + i * step, y), sym, fill=BLACK, font=f)
            cx += (shown - 1) * step + gw + 3
            if cx > limit:
                break
        if adv:
            draw.text((cx + 2, y + 2), adv, fill=BLACK, font=self.f_mac_small)

    def _player_box(self, img: Image.Image, draw: ImageDraw.ImageDraw, box,
                    name: str, captured: list, adv: str = "",
                    move: Optional[tuple] = None,
                    text_lines: Optional[list[str]] = None) -> None:
        """Compact side box hugging a board wedge. Geneva is fixed-width, so
        everything is laid out multiline to fit the narrow column: the name
        wraps (<=2 lines), captured pieces wrap into a glyph grid, and the
        bottom shows either `text_lines` (e.g. game-over summary) or a `move`
        = (prefix, san, white) drawn with a NATIVE 2D piece sprite."""
        x0, y0, x1, y1 = box
        self._mac_box(draw, box)
        pad, lh = 5, 16
        iw = x1 - x0 - 2 * pad
        left = x0 + pad
        yy = y0 + pad
        # name — wrapped, up to 2 lines
        for ln in self._wrap(draw, name or "—", self.f_mac, iw, max_lines=2):
            self.text(draw, (left, yy), ln, self.f_mac)
            yy += lh
        # material advantage "+N"
        if adv:
            self.text(draw, (left, yy), adv, self.f_mac_small)
            yy += 14
        # reserve the bottom for the move row / text lines
        tlines = [t for t in (text_lines or []) if t]
        move_h = 34 if (move and (move[1] or move[0])) else 0
        foot_h = move_h + len(tlines) * 13
        cap_bottom = y1 - pad - foot_h
        # captured pieces — Unicode glyphs, wrapped into a multiline grid
        if captured:
            f = self.f_glyph
            gw = max(12, self.text_w(draw, self._cap_glyph("Q", captured[0][1]), f))
            cols = max(1, iw // gw)
            c, rowtop = 0, yy
            for (letter, white) in captured:
                if rowtop + gw > cap_bottom:
                    break
                draw.text((left + c * gw, rowtop), self._cap_glyph(letter, white),
                          fill=BLACK, font=f)
                c += 1
                if c >= cols:
                    c, rowtop = 0, rowtop + gw
        # text lines (e.g. game over), then the move row at the very bottom
        fy = y1 - pad - foot_h
        for ln in tlines:
            self.text(draw, (left, fy), self._fit(draw, ln, self.f_mac_small, iw),
                      self.f_mac_small)
            fy += 13
        if move_h:
            prefix, san, white = move
            mx, sh = left, 16
            if self._san2d_lead(san or ""):
                sp = self._glyph2d_img(san[0], white)
                if sp is not None:
                    sh = sp.height
            pf = self.f_mac_title                          # Chicago: bigger + has accents
            if prefix:
                self.text(draw, (mx, fy + max(0, (sh - pf.size) // 2)), prefix, pf)
                mx += self.text_w(draw, prefix, pf) + 3
            if san:
                self.draw_san2d(img, draw, mx, fy, san, white, (left + iw) - mx)
            else:
                self.text(draw, (mx, fy + max(0, (sh - pf.size) // 2)), "—", pf)

    # ---- text -----------------------------------------------------------
    def text(self, draw: ImageDraw.ImageDraw, xy, s: str, font, anchor=None,
             align="left") -> None:
        if self._is_bitmap(font):
            s = self._lat1(s)
        draw.text(xy, s, fill=BLACK, font=font, anchor=anchor, align=align)

    def text_w(self, draw: ImageDraw.ImageDraw, s: str, font) -> int:
        try:
            l, t, r, b = draw.textbbox((0, 0), s, font=font)
            return r - l
        except Exception:
            return len(s) * 8

    def text_centered(self, draw: ImageDraw.ImageDraw, cx: int, y: int, s: str, font) -> None:
        if self._is_bitmap(font):
            s = self._lat1(s)
        w = self.text_w(draw, s, font)
        draw.text((cx - w // 2, y), s, fill=BLACK, font=font)

    # ---- chrome-ish UI primitives (mesh + crisp) -----------------------
    def panel(self, img, draw, box, level=255, outline=True, radius=0):
        """Filled (mesh) rounded/plain rectangle with an optional black outline."""
        if level < 255:
            self.mesh_fill(img, box, level)
        if outline:
            if radius > 0:
                draw.rounded_rectangle(list(box), radius=radius, outline=BLACK)
            else:
                draw.rectangle(list(box), outline=BLACK)

    def button(self, img, draw, box, number: Optional[int], label: str,
               enabled: bool = True, radius: int = 8, pressed: bool = False) -> None:
        # Disabled = nothing drawn. Slot positions are fixed by contract, so
        # the user doesn't need a hollow placeholder; an empty cell reads as
        # "this slot does nothing here."
        if not enabled:
            return
        x0, y0, x1, y1 = box
        if pressed:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=BLACK)
            text_color = WHITE
        else:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=BLACK)
            text_color = BLACK
        if number is not None:
            draw.text((x0 + 6, y0 + 4), str(number), fill=text_color, font=self.f_tiny)
        if label and label.strip():
            cx = (x0 + x1) // 2
            cy = y0 + (y1 - y0) // 2 - 8
            s = label[:12]
            if self._is_bitmap(self.f_small):
                s = self._lat1(s)
            w = self.text_w(draw, s, self.f_small)
            draw.text((cx - w // 2, cy), s, fill=text_color, font=self.f_small)

    def header(self, img, draw, title: str) -> None:
        self.mesh_fill(img, (0, 0, self.width, self.HEADER_H), 210)
        draw.line([0, self.HEADER_H - 1, self.width, self.HEADER_H - 1], fill=BLACK)
        self.text_centered(draw, self.width // 2, (self.HEADER_H - 26) // 2, title, self.f_large)

    def footer_buttons(self, img, draw, labels: list[str]) -> None:
        """Bottom strip of 8 soft-button labels (labels may be shorter)."""
        top = self.height - self.FOOTER_H
        self.mesh_fill(img, (0, top, self.width, self.height), 210)
        draw.line([0, top, self.width, top], fill=BLACK)
        slot = (self.width - 2 * self.MARGIN) // 8
        y0 = top + 8
        y1 = self.height - 8
        for i in range(8):
            x0 = self.MARGIN + i * slot
            x1 = x0 + slot - 6
            lab = labels[i] if i < len(labels) else ""
            self.button(img, draw, (x0, y0, x1, y1), i + 1, lab,
                        enabled=bool(lab and lab.strip()))

    def content_bounds(self) -> tuple[int, int, int, int]:
        return (self.MARGIN, self.HEADER_H + self.MARGIN,
                self.width - self.MARGIN, self.height - self.FOOTER_H - self.MARGIN)

    # =====================================================================
    # Screens
    # =====================================================================
    def render_setup_screen(self, config_url: str) -> bytes:
        import qrcode

        img = self._canvas()
        draw = ImageDraw.Draw(img)

        # vintage-Mac chrome, same as PLAY / NEW MATCH
        self._top_bar(draw, "Configuração", font=self.f_mac_title)

        # one centred Mac dialog: instructions on the left, QR on the right
        bx0, by0, bx1, by1 = 40, 92, 760, 396
        self._mac_box(draw, (bx0, by0, bx1, by1), width=2)

        qr = qrcode.QRCode(version=1,
                           error_correction=qrcode.constants.ERROR_CORRECT_L,
                           box_size=6, border=2)
        qr.add_data(config_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
        qx = bx1 - 28 - qr_img.width
        qy = by0 + (by1 - by0 - qr_img.height) // 2
        img.paste(qr_img, (qx, qy))

        tx = bx0 + 28
        ty = by0 + 28
        draw.text((tx, ty), "Nenhuma conta", fill=BLACK, font=self.f_mac_title)
        draw.text((tx, ty + 28), "Lichess configurada.", fill=BLACK, font=self.f_mac_title)
        body = ["Escaneie o QR code ou abra",
                "o endereço abaixo para",
                "configurar sua conta:"]
        yy = ty + 76
        for ln in body:
            draw.text((tx, yy), ln, fill=BLACK, font=self.f_mac)
            yy += 22
        draw.text((tx, yy + 8),
                  self._fit(draw, config_url, self.f_mac_small, qx - tx - 16),
                  fill=BLACK, font=self.f_mac_small)

        # 8-cell Mac footer (all empty: setup has no soft-key actions)
        self._play_footer(img, draw, [])
        return self.finalize(img)

    # ---- SAN / glyph-aware labels --------------------------------------
    _PIECE_LETTERS = set("KQRBNP")

    def _glyph_symbol(self, letter: str, white: bool) -> str:
        return letter.upper() if white else letter.lower()

    def draw_san(self, img, draw, x: int, y: int, san: str, white: bool,
                 font, glyph_px: int) -> int:
        """Draw a SAN-ish string starting at (x, top=y). If it begins with a
        piece letter (KQRBN) draw that piece glyph, then the rest as text.
        Returns the total advanced width."""
        if not san:
            return 0
        cx = x
        rest = san
        if san[0] in self._PIECE_LETTERS and not san.startswith("O-O"):
            sym = self._glyph_symbol(san[0], white)
            self.paste_piece(img, sym, (cx, y, cx + glyph_px, y + glyph_px))
            cx += glyph_px
            rest = san[1:]
        if rest:
            # vertically center text against the glyph box
            ty = y + max(0, (glyph_px - font.size) // 2)
            draw.text((cx, ty), rest, fill=BLACK, font=font)
            cx += self.text_w(draw, rest, font)
        return cx - x

    def san_width(self, draw, san: str, font, glyph_px: int) -> int:
        if not san:
            return 0
        w = 0
        rest = san
        if san[0] in self._PIECE_LETTERS and not san.startswith("O-O"):
            w += glyph_px
            rest = san[1:]
        if rest:
            w += self.text_w(draw, rest, font)
        return w

    def draw_san_centered(self, img, draw, cx: int, y: int, san: str, white: bool,
                          font, glyph_px: int) -> None:
        w = self.san_width(draw, san, font, glyph_px)
        self.draw_san(img, draw, cx - w // 2, y, san, white, font, glyph_px)

    def _san2d_lead(self, san: str) -> bool:
        """True if `san` starts with a piece letter that gets a 2D sprite."""
        return bool(san) and san[0] in self._PIECE_LETTERS and not san.startswith("O-O")

    def _san2d_width(self, draw, san: str, white: bool) -> int:
        if not san:
            return 0
        w, rest = 0, san
        if self._san2d_lead(san):
            sp = self._glyph2d_img(san[0], white)          # native size
            if sp is not None:
                w += sp.width + 2
                rest = san[1:]
        if rest:
            w += self.text_w(draw, rest, self.f_mac_title)
        return w

    def draw_san2d(self, img, draw, x: int, y: int, san: str, white: bool,
                   max_w: int) -> int:
        """Draw a SAN-ish move with a NATIVE-size 2D piece sprite for the leading
        piece, then the rest in Chicago (sized to sit alongside the big glyph,
        and it has the accents Geneva's bitmap lacks), vertically centred on the
        sprite. `y` is the sprite top; returns the advance width. Clips to max_w."""
        if not san:
            return 0
        cx, rest, sh = x, san, 16
        if self._san2d_lead(san):
            sp = self._glyph2d_img(san[0], white)          # native, never resized
            if sp is not None:
                img.paste(sp.convert("L"), (int(cx), int(y)), sp.split()[3])
                cx += sp.width + 2
                sh = sp.height
                rest = san[1:]
        if rest:
            f = self.f_mac_title
            rest = self._fit(draw, rest, f, max(8, (x + max_w) - cx))
            self.text(draw, (cx, y + max(0, (sh - f.size) // 2)), rest, f)
            cx += self.text_w(draw, rest, f)
        return cx - x

    # ---- PLAY screen ----------------------------------------------------
    # ---- board geometry: parameterised one-point perspective ------------
    def _init_board_geometry(self) -> None:
        """Chessmaster-2000-style projection. All five constants are tunable;
        keeping it a function (not baked coords) makes the look easy to retune."""
        self.CX = 382          # board centre x
        self.Y_FAR = 132       # far (rank-8 / top) surface edge
        self.Y_NEAR = 398      # near (rank-1 / front) surface edge
        self.W_FAR = 356       # far-edge width  (wider -> gentler tilt)
        self.W_NEAR = 632      # near-edge width
        self.ROW_RATIO = 1.07  # rank foreshortening (front ranks taller)
        self.SIDE_H = 14       # 3D front-lip thickness
        self.PIECE_LIFT = 6    # piece base sits this far below square centre
        hs = [self.ROW_RATIO ** i for i in range(8)]   # dr 0 far(small)..7 near
        scale = (self.Y_NEAR - self.Y_FAR) / sum(hs)
        ys, acc = [self.Y_FAR], self.Y_FAR
        for h in hs:
            acc += h * scale
            ys.append(acc)
        self._row_ys = ys

    def _edges_at(self, y):
        s = (y - self.Y_FAR) / (self.Y_NEAR - self.Y_FAR)
        half = (self.W_FAR + (self.W_NEAR - self.W_FAR) * s) / 2.0
        return self.CX - half, self.CX + half

    def _sq_quad(self, dc, dr):
        yt, yb = self._row_ys[dr], self._row_ys[dr + 1]
        lt, rt = self._edges_at(yt)
        lb, rb = self._edges_at(yb)
        xTL = lt + dc / 8 * (rt - lt); xTR = lt + (dc + 1) / 8 * (rt - lt)
        xBL = lb + dc / 8 * (rb - lb); xBR = lb + (dc + 1) / 8 * (rb - lb)
        return [(xTL, yt), (xTR, yt), (xBR, yb), (xBL, yb)]

    def _sq_anchor(self, dc, dr):
        q = self._sq_quad(dc, dr)
        cx = sum(p[0] for p in q) / 4.0
        cy = sum(p[1] for p in q) / 4.0
        return cx, cy + self.PIECE_LIFT

    @functools.lru_cache(maxsize=1)
    def _gray_pattern(self) -> Image.Image:
        """Screen-aligned 50% Mac 'gray' (deterministic checker), pure 0/255."""
        w, h = self.width, self.height
        even = bytes(255 if (x & 1) == 0 else 0 for x in range(w))
        odd = bytes(0 if (x & 1) == 0 else 255 for x in range(w))
        data = b"".join(even if (y & 1) == 0 else odd for y in range(h))
        return Image.frombytes("L", (w, h), data)

    # ---- CP2150 piece sprites (never resized) ---------------------------
    @staticmethod
    def _alpha_from_bg(im: Image.Image) -> Image.Image:
        """Derive alpha by flood-filling the border-connected near-white
        background to transparent, preserving interior white highlights. Safety
        net for not-yet-cleaned sprites; a no-op on sprites with real alpha."""
        rgb = im.convert("RGB")
        w, h = rgb.size
        marker = (255, 0, 255)
        seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                 (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
        for s in seeds:
            if sum(rgb.getpixel(s)) > 600:
                ImageDraw.floodfill(rgb, s, marker, thresh=60)
        px = rgb.load()
        a = Image.new("L", (w, h), 255)
        ap = a.load()
        for y in range(h):
            for x in range(w):
                if px[x, y] == marker:
                    ap[x, y] = 0
        return a

    def _cp_sprite(self, symbol: str):
        sp = self._cp_cache.get(symbol)
        if sp is None:
            im = Image.open(_ASSET_DIR / (_SYM2FILE[symbol] + ".png")).convert("RGBA")
            a = im.split()[3]
            if a.getextrema() == (255, 255):     # opaque -> derive transparency
                a = self._alpha_from_bg(im)
            L = im.convert("L")
            a = a.point(lambda v: 255 if v > 40 else 0)
            sp = (L, a)
            self._cp_cache[symbol] = sp
        return sp

    # ---- Mac chrome helpers ---------------------------------------------
    def _cap_glyph(self, letter: str, white: bool) -> str:
        return chr((0x2654 if white else 0x265A) + _GORD[letter])

    # Typographic chars the Geneva *bitmap* fonts (ImageFont.load, no FreeType)
    # can't encode: they are latin-1-only and raise UnicodeEncodeError on any
    # codepoint > U+00FF. Map the common ones to safe ASCII; anything else -> '?'.
    _LAT1_MAP = {
        "•": "-", "—": "-", "–": "-", "…": "...",
        "“": '"', "”": '"', "‘": "'", "’": "'",
        "×": "x", "→": "->", " ": " ",
    }

    def _lat1(self, s: str) -> str:
        """Transliterate `s` to a latin-1-safe string for the bitmap fonts.
        Latin-1 accents (Portuguese á/ç/ã, U+0080..U+00FF) pass through."""
        if all(ord(c) < 256 for c in s):
            return s
        return "".join(c if ord(c) < 256 else self._LAT1_MAP.get(c, "?")
                       for c in s)

    @staticmethod
    def _is_bitmap(font) -> bool:
        return not isinstance(font, ImageFont.FreeTypeFont)

    def _fit(self, draw, s: str, font, max_w: int) -> str:
        if self._is_bitmap(font):
            s = self._lat1(s)
        ell = "..." if self._is_bitmap(font) else "…"
        if self.text_w(draw, s, font) <= max_w:
            return s
        while s and self.text_w(draw, s + ell, font) > max_w:
            s = s[:-1]
        return s + ell

    def _wrap(self, draw, s: str, font, max_w: int, max_lines: int = 99) -> list[str]:
        """Word-wrap `s` to `max_w` px (Geneva is fixed-width, so plan multiline).
        A word wider than the box is hard-split; overflow past `max_lines` is
        dropped with the last shown line ellipsised."""
        if self._is_bitmap(font):
            s = self._lat1(s)
        lines: list[str] = []
        for raw in s.split():
            word = raw
            while word:
                if lines and self.text_w(draw, lines[-1] + " " + word, font) <= max_w:
                    lines[-1] += " " + word
                    word = ""
                elif self.text_w(draw, word, font) <= max_w:
                    lines.append(word)
                    word = ""
                else:                                   # single word too wide: hard-split
                    cut = len(word)
                    while cut > 1 and self.text_w(draw, word[:cut], font) > max_w:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                if len(lines) > max_lines:
                    return [*lines[:max_lines - 1],
                            self._fit(draw, lines[max_lines - 1], font, max_w)]
        return lines if lines else [""]

    def _mac_box(self, draw, box, shadow=True, width=2) -> None:
        x0, y0, x1, y1 = box
        if shadow:
            draw.rectangle([x0 + 3, y0 + 3, x1 + 3, y1 + 3], fill=BLACK)
        draw.rectangle([x0, y0, x1, y1], fill=WHITE, outline=BLACK, width=width)

    def _draw_flag(self, draw, x, y) -> None:
        draw.line([(x, y), (x, y + 16)], fill=BLACK, width=2)
        draw.polygon([(x + 2, y), (x + 13, y + 4), (x + 2, y + 8)], fill=BLACK)

    def _mac_button(self, img, draw, box, index: int, token,
                    pressed: bool = False) -> None:
        token = token or ()
        label = token[0] if token else ""
        white = bool(token[1]) if len(token) > 1 else True
        is_move = bool(token[2]) if len(token) > 2 else False
        # Empty = no chrome at all. Slot positions are contractual, so a
        # hollow Mac box + index number on an unused slot is just noise.
        if not (label and label.strip()):
            return
        x0, y0, x1, y1 = box
        if pressed:
            # Mac "down" look: filled-black cell with white outline + text.
            draw.rectangle(box, fill=BLACK)
            draw.rectangle(box, outline=WHITE, width=1)
            text_color = WHITE
        else:
            self._mac_box(draw, box, shadow=True, width=2)
            text_color = BLACK
        draw.text((x0 + 4, y0 + 2), str(index), fill=text_color, font=self.f_mac_small)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(ord(ch) > 0x2000 for ch in label):     # flag / icon glyph
            # In pressed mode the black flag would vanish into the black bg;
            # skip the glyph — index + the cell highlight already say "pressed".
            if not pressed:
                self._draw_flag(draw, int(cx - 6), int(cy - 8))
            return
        if is_move and self._san2d_lead(label):        # move w/ a piece -> 2D sprite
            if not pressed:
                w = self._san2d_width(draw, label, white)
                sp = self._glyph2d_img(label[0], white)
                sh = sp.height if sp is not None else 16
                self.draw_san2d(img, draw, int(cx - w / 2), int(cy - sh / 2),
                                label, white, (x1 - x0) - 6)
                return
            # Pressed move button: drop the sprite, show only the destination
            # text in white so something still reads in the inverted cell.
            text = label[1:] if len(label) > 1 else label
            f = self.f_mac
            bb = draw.textbbox((0, 0), text, font=f)
            draw.text((cx - (bb[2] - bb[0]) / 2 - bb[0],
                       cy - (bb[3] - bb[1]) / 2 - bb[1]),
                      text, fill=text_color, font=f)
            return
        # plain-text buttons (pawn SAN, file/rank selects, O-O, actions) use
        # Chicago to match the move buttons; long labels (CONFIRMAR/CANCELAR)
        # fall back to the narrower Geneva so they still fit the cell.
        f = self.f_mac_title
        if self.text_w(draw, label, f) > (x1 - x0) - 8:
            f = self.f_mac
        bb = draw.textbbox((0, 0), label, font=f)
        draw.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]),
                  label, fill=text_color, font=f)

    def _last_san(self, view: dict):
        san = view.get("last_move_san")
        if san:
            return san
        for _num, w, b in reversed(view.get("moves", [])):
            if b:
                return b
            if w:
                return w
        return None

    # =====================================================================
    # PLAY screen — Chess Player 2150 sprites + Chessmaster perspective board
    # =====================================================================
    def render_play(self, view: dict) -> bytes:
        """Dispatch the PLAY screen to the renderer matching view["mode"].

        Supported modes:
          - "3d" (default): Chessmaster-2000 perspective with chess-2150
            sprites composited far->near. The original look.
          - "2d": flat top-down board using the existing 2D sprites
            (assets/glyphs2d), single right column with opponent header,
            moves list, and a "Sua jogada:" preview panel.

        Both modes share the top button strip and 8-button Mac footer.
        """
        if view.get("mode") == "2d":
            return self._render_play_2d(view)
        return self._render_play_3d(view)

    def _render_play_3d(self, view: dict) -> bytes:
        """Chessmaster-2000-style perspective board (the original render).

        view keys: title, orientation_white, squares[(dc,dr,symbol|None,dark)],
        last_move[(dc,dr)], loser_king|None, rank_labels[8], file_labels[8],
        adversary_name, adversary_captured[(LETTER,white)], move_title,
        move_preview, preview_lines|None, last_move_san(optional),
        buttons[10] of (label, white, is_move).
        """
        img = self._canvas()
        draw = ImageDraw.Draw(img)
        gray = self._gray_pattern()
        sq = {(dc, dr): (sym, dark) for (dc, dr, sym, dark) in view["squares"]}

        # ---- board surface: dark squares dithered, light squares white ----
        dark_mask = Image.new("1", (self.width, self.height), 0)
        dm = ImageDraw.Draw(dark_mask)
        for dr in range(8):
            for dc in range(8):
                if sq[(dc, dr)][1]:
                    dm.polygon([(round(x), round(y)) for x, y in self._sq_quad(dc, dr)],
                               fill=1)
        # 3D front lip is dithered too
        nl, nr = self._edges_at(self.Y_NEAR)
        lip = [(nl, self.Y_NEAR), (nr, self.Y_NEAR),
               (nr, self.Y_NEAR + self.SIDE_H), (nl, self.Y_NEAR + self.SIDE_H)]
        dm.polygon([(round(x), round(y)) for x, y in lip], fill=1)
        img.paste(gray, (0, 0), dark_mask)

        # ---- grid + outer frame ----
        for dr in range(9):
            y = self._row_ys[dr]
            l, r = self._edges_at(y)
            draw.line([(l, y), (r, y)], fill=BLACK, width=1)
        top = self._edges_at(self.Y_FAR); bot = self._edges_at(self.Y_NEAR)
        for c in range(9):
            xt = top[0] + c / 8 * (top[1] - top[0])
            xb = bot[0] + c / 8 * (bot[1] - bot[0])
            draw.line([(xt, self.Y_FAR), (xb, self.Y_NEAR)], fill=BLACK, width=1)
        fl, fr = top;
        draw.line([(fl, self.Y_FAR), (fr, self.Y_FAR)], fill=BLACK, width=3)
        draw.line([(nl, self.Y_NEAR), (nr, self.Y_NEAR)], fill=BLACK, width=3)
        draw.line([(fl, self.Y_FAR), (nl, self.Y_NEAR)], fill=BLACK, width=3)
        draw.line([(fr, self.Y_FAR), (nr, self.Y_NEAR)], fill=BLACK, width=3)
        draw.polygon([(round(x), round(y)) for x, y in lip], outline=BLACK)
        draw.line([(nl, self.Y_NEAR + self.SIDE_H), (nr, self.Y_NEAR + self.SIDE_H)],
                  fill=BLACK, width=2)

        # ---- last-move highlight (before pieces) ----
        for (dc, dr) in view.get("last_move", []):
            q = self._sq_quad(dc, dr)
            draw.polygon([(round(x), round(y)) for x, y in q], outline=BLACK)
            draw.line([q[0], q[1]], fill=BLACK, width=3)
            draw.line([q[3], q[2]], fill=BLACK, width=3)

        # ---- pieces, painted far -> near ----
        for dr in range(8):
            for dc in range(8):
                symbol = sq[(dc, dr)][0]
                if not symbol:
                    continue
                L, a = self._cp_sprite(symbol)
                ax, ay = self._sq_anchor(dc, dr)
                img.paste(L, (int(round(ax - L.width / 2)), int(round(ay - L.height))), a)

        # ---- loser king X ----
        lk = view.get("loser_king")
        if lk:
            q = self._sq_quad(lk[0], lk[1])
            draw.line([q[0], q[2]], fill=BLACK, width=4)
            draw.line([q[1], q[3]], fill=BLACK, width=4)

        # ---- edge coordinates ----
        rl = view.get("rank_labels", [])
        fl_lab = view.get("file_labels", [])
        for dr in range(8):
            yt, yb = self._row_ys[dr], self._row_ys[dr + 1]
            l, _ = self._edges_at((yt + yb) / 2)
            if dr < len(rl):
                draw.text((l - 16, (yt + yb) / 2 - 7), str(rl[dr]),
                          fill=BLACK, font=self.f_mac_small)
        cy = self.Y_NEAR + self.SIDE_H + 2
        for dc in range(8):
            if dc < len(fl_lab):
                q = self._sq_quad(dc, 7)
                cx = (q[2][0] + q[3][0]) / 2
                self.text_centered(draw, int(cx), int(cy), str(fl_lab[dc]), self.f_mac_small)

        b = view.get("buttons", [])

        # ---- top strip: 4 corner action buttons + title ----
        self._play_top_strip(img, draw, view)

        # ---- player boxes: opponent (left wedge) + me (right wedge). Short &
        # narrow (y=56..196, x=6..134 / 666..794 = 128x140): the wedge is widest
        # near the top, so at the box bottom (y=196) the board edges are x~171
        # (left) / x~593 (right) -> ample clearance. The opponent box carries
        # the last move; the me box carries my composing move ("my play" lives
        # at the side, not in a top banner). ----
        last_san = self._last_san(view)
        self._player_box(img, draw, (6, 56, 134, 196),
                         view.get("adversary_name", ""),
                         view.get("adversary_captured", []),
                         view.get("adversary_adv", ""),
                         move=("Últ:", last_san or "",
                               view.get("last_move_white", False)))

        plines = view.get("preview_lines")
        if plines:
            me_move, me_text = None, plines[:2]
        else:
            me_move = ("", view.get("move_preview", "") or "",
                       view.get("preview_white", True))
            me_text = None
        self._player_box(img, draw, (666, 56, 794, 196),
                         view.get("user_name", ""),
                         view.get("user_captured", []),
                         view.get("user_adv", ""),
                         move=me_move, text_lines=me_text)

        # ---- bottom footer: physical buttons B1..B8 ----
        self._play_footer(img, draw, b)
        return self.finalize(img)

    # ---- 2D top-down board (alternative PLAY layout) -------------------
    # Visual reference: output/pil_play.png. Flat 8x8 board on the left
    # using the existing 2D sprites (assets/glyphs2d), single right column
    # for opponent header / moves list / next-move preview.

    _2D_SQ = 44                          # square size (px) - 8 = 352 board
    _2D_BOARD_X0 = 22                    # board left edge (file-label margin)
    _2D_BOARD_Y0 = 62                    # board top edge (below top strip)

    def _2d_sq_box(self, dc: int, dr: int) -> tuple[int, int, int, int]:
        s = self._2D_SQ
        x0 = self._2D_BOARD_X0 + dc * s
        y0 = self._2D_BOARD_Y0 + dr * s
        return (x0, y0, x0 + s, y0 + s)

    def _render_play_2d(self, view: dict) -> bytes:
        img = self._canvas()
        draw = ImageDraw.Draw(img)
        gray = self._gray_pattern()
        sq = {(dc, dr): (sym, dark) for (dc, dr, sym, dark) in view["squares"]}
        s = self._2D_SQ
        bx0, by0 = self._2D_BOARD_X0, self._2D_BOARD_Y0
        bx1, by1 = bx0 + s * 8, by0 + s * 8

        # 1) dark squares — same dot-mesh gray as the 3D path so the two views
        #    are visually consistent.
        dark_mask = Image.new("1", (self.width, self.height), 0)
        dm = ImageDraw.Draw(dark_mask)
        for dr in range(8):
            for dc in range(8):
                if sq[(dc, dr)][1]:
                    x0, y0, x1, y1 = self._2d_sq_box(dc, dr)
                    dm.rectangle([x0, y0, x1 - 1, y1 - 1], fill=1)
        img.paste(gray, (0, 0), dark_mask)

        # 2) grid lines + outer frame
        for i in range(9):
            draw.line([bx0, by0 + i * s, bx1, by0 + i * s], fill=BLACK, width=1)
            draw.line([bx0 + i * s, by0, bx0 + i * s, by1], fill=BLACK, width=1)
        draw.rectangle([bx0 - 2, by0 - 2, bx1 + 2, by1 + 2],
                       outline=BLACK, width=3)

        # 3) last-move highlight (thick outline, drawn before pieces)
        for (dc, dr) in view.get("last_move", []):
            x0, y0, x1, y1 = self._2d_sq_box(dc, dr)
            draw.rectangle([x0 + 1, y0 + 1, x1 - 2, y1 - 2],
                           outline=BLACK, width=3)

        # 4) pieces — native-size 2D sprites, centred in each square. The
        #    sprites are crafted for inline label use; at the board scale
        #    they sit comfortably (sprite ~30px on a 44px square).
        for dr in range(8):
            for dc in range(8):
                symbol = sq[(dc, dr)][0]
                if not symbol:
                    continue
                white = symbol.isupper()
                im2d = self._glyph2d_img(symbol.lower(), white)
                if im2d is None:
                    continue
                x0, y0, x1, y1 = self._2d_sq_box(dc, dr)
                px = x0 + (s - im2d.width) // 2
                py = y0 + (s - im2d.height) // 2
                img.paste(im2d, (px, py), im2d)

        # 5) loser-king X over the mated square
        lk = view.get("loser_king")
        if lk:
            x0, y0, x1, y1 = self._2d_sq_box(lk[0], lk[1])
            draw.line([x0 + 4, y0 + 4, x1 - 5, y1 - 5], fill=BLACK, width=3)
            draw.line([x1 - 5, y0 + 4, x0 + 4, y1 - 5], fill=BLACK, width=3)

        # 6) edge coordinates (rank labels on the left, file labels under)
        rl = view.get("rank_labels", [])
        fl_lab = view.get("file_labels", [])
        for dr in range(8):
            if dr < len(rl):
                self.text(draw, (bx0 - 14, by0 + dr * s + s // 2 - 7),
                          str(rl[dr]), self.f_mac_small)
        for dc in range(8):
            if dc < len(fl_lab):
                cx = bx0 + dc * s + s // 2
                self.text_centered(draw, cx, by1 + 3, str(fl_lab[dc]),
                                   self.f_mac_small)

        # 7) Top strip — 4 corner action buttons + title
        b = view.get("buttons", [])
        self._play_top_strip(img, draw, view)

        # 8) Right info column: opponent header, moves list, "Sua jogada"
        px0, px1 = bx1 + 18, self.width - 12
        self._play_2d_opponent_block(img, draw, view, px0, 60, px1, 116)
        self._play_2d_moves_block(img, draw, view, px0, 124, px1, 348)
        self._play_2d_preview_block(img, draw, view, px0, 356, px1, 422)

        # 9) Bottom footer (same as 3D path)
        self._play_footer(img, draw, b)
        return self.finalize(img)

    # ---- 2D right-column blocks ----------------------------------------

    def _play_2d_opponent_block(self, img, draw, view, x0, y0, x1, y1) -> None:
        """Top right block: opponent name + captured-pieces row."""
        pad = 4
        name = view.get("adversary_name", "") or "—"
        f = self.f_mac
        self.text(draw, (x0 + pad, y0 + pad),
                  self._fit(draw, name, f, x1 - x0 - 2 * pad), f)
        # captured pieces row right below the name
        cy = y0 + pad + 18
        cx = x0 + pad
        cap = view.get("adversary_captured", [])
        for letter, white in cap:
            im = self._glyph2d_img(letter.lower(), white)
            if im is None:
                break
            if cx + im.width > x1 - pad:
                break
            img.paste(im, (cx, cy), im)
            cx += im.width + 1
        adv = view.get("adversary_adv", "")
        if adv:
            self.text(draw, (cx + 4, cy + 4), adv, self.f_mac_small)
        # divider under
        draw.line([x0, y1, x1, y1], fill=BLACK, width=1)

    def _play_2d_moves_block(self, img, draw, view, x0, y0, x1, y1) -> None:
        """Middle block: last N moves in two SAN columns."""
        f_num = self.f_mac_small
        moves = view.get("moves", []) or []
        # Choose the last N rows that fit
        row_h = 18
        max_rows = max(1, (y1 - y0 - 4) // row_h)
        rows = moves[-max_rows:]
        if not rows:
            self.text(draw, (x0 + 6, y0 + 4), "—", self.f_mac)
            return
        col_w = (x1 - x0 - 30) // 2
        for i, (num, wsan, bsan) in enumerate(rows):
            yy = y0 + i * row_h
            self.text(draw, (x0 + 2, yy + 2), f"{num}.", f_num)
            if wsan:
                self.draw_san2d(img, draw, x0 + 28, yy, wsan, True, col_w)
            if bsan:
                self.draw_san2d(img, draw, x0 + 28 + col_w + 10, yy, bsan,
                                False, col_w)

    def _play_2d_preview_block(self, img, draw, view, x0, y0, x1, y1) -> None:
        """Bottom right block: 'Sua jogada:' with the composing move and
        a dashed Mac box framing it."""
        # Dashed-ish frame: thin Mac box (no shadow) gets us most of the way
        # without a new primitive.
        self._mac_box(draw, (x0, y0, x1, y1), shadow=False, width=1)
        cx = (x0 + x1) // 2
        # "Sua jogada:" label at the top of the box
        self.text(draw, (x0 + 8, y0 + 6), "Sua jogada:", self.f_mac_small)
        # The composing move (or game-over text) sits centred underneath.
        plines = view.get("preview_lines")
        if plines:
            ly = y0 + 24
            for ln in plines[:2]:
                lw = self.text_w(draw, ln, self.f_mac_title)
                self.text(draw, (cx - lw // 2, ly), ln, self.f_mac_title)
                ly += 18
            return
        san = view.get("move_preview", "") or "—"
        white = view.get("preview_white", True)
        ay = y0 + 24
        if self._san2d_lead(san):
            w = self._san2d_width(draw, san, white)
            self.draw_san2d(img, draw, cx - w // 2, ay, san, white, x1 - x0 - 16)
        else:
            f = self.f_mac_title
            lw = self.text_w(draw, san, f)
            self.text(draw, (cx - lw // 2, ay), san, f)

    # ---- shared 8-cell device-button grid ------------------------------
    _BTN_N = 8
    _BTN_GAP = 6

    def _btn_cell(self, i: int) -> tuple[float, float]:
        """(x0, x1) of physical-button cell i (0..7) on the 8-cell strip."""
        bw = (self.width - self._BTN_GAP * (self._BTN_N + 1)) / self._BTN_N
        x0 = self._BTN_GAP + i * (bw + self._BTN_GAP)
        return x0, x0 + bw

    def _play_top_strip(self, img, draw, view: dict) -> None:
        """PLAY-screen top strip: 4 corner action buttons + centred title.

        Slots 0 and 7 (the outer-most) plus slots 1 and 6 are the four
        device top buttons (HL_LEFT / ESC / ENTER / HL_RIGHT in the
        contract slot order). Slots 2..5 carry the title text.

        Labels (current contract):
          - slot 0 (HL_LEFT)  : view-toggle target — "2D" while showing
            3D, "3D" while showing 2D.
          - slot 1 (ESC)      : "Resign" — reserved; the actual handler
            still maps short-press ESC to whatever the screen does today.
          - slot 6 (ENTER)    : "Draw"   — same caveat.
          - slot 7 (HL_RIGHT) : blank — reserved.
        """
        title = view.get("title", "")
        view_label = "3D" if view.get("mode") == "2d" else "2D"
        y0, y1 = 4, 48

        # Four corner buttons. _mac_button skips slots whose token has no
        # label, which matches the spec ("disabled = empty space").
        for slot, label in ((0, view_label), (1, "Resign"),
                            (6, "Draw"), (7, "")):
            x0, x1 = self._btn_cell(slot)
            self._mac_button(img, draw,
                             (round(x0), y0, round(x1), y1),
                             slot + 9, (label,) if label else None)

        # Centred title across the middle 4 slots. We don't draw a box
        # here — the corner buttons are the visual anchors and we don't
        # want the title to look "tied" to slot 0 or 7.
        _, lx = self._btn_cell(1)
        rx, _ = self._btn_cell(6)
        if title:
            f = self.f_mac_title
            tw = self.text_w(draw, title, f)
            cx = (round(lx) + round(rx)) // 2
            self.text(draw, (cx - tw // 2, (y0 + y1 - f.size) // 2 + 1),
                      self._fit(draw, title, f, int(rx - lx) - 12), f)

    def _top_bar(self, draw, title: str, font=None) -> None:
        """Top strip = one instruction bar spanning the full 8-button width,
        grid-aligned to the footer. Top physical buttons are btn9..btn16
        (footer = btn1..btn8). Per the device button contract (docs/
        INTERFACE_DESIGN.md §4) the LEFTMOST top key (btn9) is the only
        device-local key in normal operation: a single press toggles the
        device-local overlay, which takes over the WHOLE top row and the whole
        screen; pressing it again hides the overlay and btn10..btn16 return to
        the app. We can't draw the overlay, but we draw a small down-chevron +
        separator at the btn9 position as its affordance; the rest of the bar
        is instruction text. `font` overrides the title face (e.g. Chicago for
        the new-game screen)."""
        font = font or self.f_mac
        y0, y1 = 4, 48
        bx0, _ = self._btn_cell(0)
        _, bx1 = self._btn_cell(self._BTN_N - 1)
        bx0, bx1 = round(bx0), round(bx1)
        self._mac_box(draw, (bx0, y0, bx1, y1))
        # menu-trigger hint: down-chevron + separator at the btn9 spot
        cy = (y0 + y1) // 2
        hx = bx0 + 16
        draw.polygon([(hx - 7, cy - 4), (hx + 7, cy - 4), (hx, cy + 5)], fill=BLACK)
        sep = bx0 + 34
        draw.line([(sep, y0 + 6), (sep, y1 - 6)], fill=BLACK, width=1)
        # instruction text fills the rest of the bar
        tx = sep + 10
        draw.text((tx, y0 + 13),
                  self._fit(draw, title, font, bx1 - tx - 12),
                  fill=BLACK, font=font)

    def _play_footer(self, img, draw, buttons: list, pressed: bool = False) -> None:
        """Bottom strip = physical buttons B1..B8 only, as Mac boxes. Move
        buttons (is_move) render the 2D piece sprite + destination text."""
        y0, y1 = 430, 474
        for i in range(self._BTN_N):
            tok = buttons[i] if i < len(buttons) else None
            x0, x1 = self._btn_cell(i)
            self._mac_button(img, draw, (round(x0), y0, round(x1), y1), i + 1, tok,
                             pressed=pressed)

    def render_pressed_footer_strip(self, buttons: list) -> Optional[bytes]:
        """Render JUST the bottom 50-px footer strip with every button in
        pressed visual state. Returns PNG bytes of an 800 x FOOTER_H image
        (rows that overlay the device's bottom strip). Returns None if no
        button slot would be populated — caller skips the upload then.

        Used for the LLSS strip-cache contract: the device caches this
        strip, then on press of slot S it picks slot S's column range and
        merges it over its captured frame band (everything else stays as
        rendered in the main frame)."""
        # Skip if there's nothing to highlight.
        populated = sum(1 for tok in buttons[:self._BTN_N]
                        if tok and (tok[0] if isinstance(tok, tuple) else tok)
                        and (tok[0].strip() if isinstance(tok, tuple) else tok.strip()))
        if populated == 0:
            return None
        img = Image.new("L", (self.width, self.FOOTER_H), WHITE)
        draw = ImageDraw.Draw(img)
        # Mirror _play_footer's button rect (y=430..474 in a 480-px canvas)
        # by collapsing to (y=0..44) in a FOOTER_H=50 strip — preserves the
        # 6-px bottom margin so the device-side overlay positions match.
        y0, y1 = 0, self.FOOTER_H - 6
        for i in range(self._BTN_N):
            tok = buttons[i] if i < len(buttons) else None
            x0, x1 = self._btn_cell(i)
            self._mac_button(img, draw, (round(x0), y0, round(x1), y1), i + 1, tok,
                             pressed=True)
        return self.finalize(img)

    def render_play_pressed_strip(self, view: dict) -> Optional[bytes]:
        """Bottom pressed-strip companion to render_play. Used by the LLSS
        strip-cache contract — the device extracts only the pressed slot's
        column range and merges it over its captured frame band."""
        return self.render_pressed_footer_strip(view.get("buttons", []))

    def render_new_match_pressed_strip(self,
                                       button_labels: list[str]) -> Optional[bytes]:
        """Bottom pressed-strip companion to render_new_match_screen."""
        tokens = [(lab,) for lab in (button_labels or [])[:8]]
        return self.render_pressed_footer_strip(tokens)

    def render_new_match_screen(self, mode: str, card_title: str, card_main: str,
                                card_sub: str, primary_action: str,
                                secondary_action: str, helper_text: str,
                                button_labels: list[str]) -> bytes:
        """Vintage-Mac new-game screen: the same chevron-menu top bar + 8-button
        footer as PLAY, with a centred Mac dialog box showing the current
        selection (adversary + color). The create action is a real physical
        button (BTN_5, shown in the footer) — there is no fake on-screen send
        button. ``mode="incoming"`` shows an accept/decline challenge dialog.
        Output is pure 1bpp B/W."""
        img = self._canvas()
        draw = ImageDraw.Draw(img)

        # top instruction bar (with hidden-menu chevron hint), same as PLAY
        self._top_bar(draw, card_title or "Novo jogo", font=self.f_mac_title)

        # ---- centred Mac dialog box ----
        bx0, by0, bx1, by1 = 150, 116, 650, 356
        self._mac_box(draw, (bx0, by0, bx1, by1), width=2)
        cxc = (bx0 + bx1) // 2
        inner = bx1 - bx0 - 48

        # selected adversary (big Chicago) + sub line (Geneva)
        self.text_centered(draw, cxc, by0 + 30,
                           self._fit(draw, card_main or "—", self.f_mac_title, inner),
                           self.f_mac_title)
        if card_sub and card_sub.strip():
            self.text_centered(draw, cxc, by0 + 80,
                               self._fit(draw, card_sub, self.f_mac, inner), self.f_mac)

        # divider
        draw.line([(bx0 + 24, by0 + 118), (bx1 - 24, by0 + 118)], fill=BLACK, width=1)

        # action hint inside the dialog
        if primary_action and primary_action.strip():
            # incoming challenge: accept/decline via ENTER/ESC
            self.text_centered(draw, cxc, by1 - 86,
                               "ENTER: " + primary_action, self.f_mac)
            if secondary_action and secondary_action.strip():
                self.text_centered(draw, cxc, by1 - 58,
                                   "ESC: " + secondary_action, self.f_mac)
        else:
            # create mode: point at the real button
            self.text_centered(draw, cxc, by1 - 70,
                               "Criar jogo:  botão 5  ou  ENTER", self.f_mac)

        if helper_text and helper_text.strip():
            self.text_centered(draw, self.width // 2, by1 + 18,
                               self._fit(draw, helper_text, self.f_mac_small, self.width - 40),
                               self.f_mac_small)

        # ---- 8-button Mac footer (BTN_1..BTN_8) ----
        tokens = [(lab,) for lab in (button_labels or [])[:8]]
        self._play_footer(img, draw, tokens)
        return self.finalize(img)
