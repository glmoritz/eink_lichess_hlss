"""Prototype v2: Bold e-ink-native PLAY screen.

Changes vs v1, per feedback:
- BOLD fonts everywhere (DejaVuSans-Bold), larger.
- No 1px strokes: panel borders 3px, board frame 4px, last-move 3px, dividers 2px,
  dashed strokes 2px, pills 3px.
- Pieces: contours thickened by dilating the dark edges of the rasterized SVG
  (render 3x, grow ink, downscale) so glyphs read solid/defined in pure B/W.

Gray board squares are still Floyd-Steinberg dithered (pleasing stipple); text and
pieces stay crisp via a final hard threshold.
"""
import functools
import io
import sys

import cairosvg
import chess
import chess.svg
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, "/app/src")
from hlss.database import SessionLocal
from hlss.models import Game
from hlss.services.renderer import RendererService

W, H = 800, 480
HEADER_H = 48
FOOTER_H = 48
DARK_GRAY = 188

FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
f_tiny = ImageFont.truetype(FB, 14)
f_small = ImageFont.truetype(FB, 17)
f_mid = ImageFont.truetype(FB, 20)
f_large = ImageFont.truetype(FB, 28)


def text_w(draw, s, font):
    l, t, r, b = draw.textbbox((0, 0), s, font=font)
    return r - l


# ---- bold, well-defined pieces -------------------------------------------
@functools.lru_cache(maxsize=128)
def bold_piece(symbol, size, grow=1):
    """Return (L, alpha) for a piece with thickened contours.

    Render the SVG at 3x, turn the dark outline+fill into an 'ink' mask, dilate
    it, then downscale. White pieces keep a white fill with a bold black contour;
    black pieces become solid filled. Reads cleanly in 1bpp.
    """
    ss = size * 3
    piece = chess.Piece.from_symbol(symbol)
    png = cairosvg.svg2png(bytestring=chess.svg.piece(piece).encode("utf-8"),
                           output_width=ss, output_height=ss)
    rgba = Image.open(io.BytesIO(png)).convert("RGBA")
    L = rgba.convert("L")
    A = rgba.split()[3]
    opaque = A.point(lambda v: 255 if v > 40 else 0)
    dark = L.point(lambda v: 255 if v < 150 else 0)
    dark = ImageChops.multiply(dark, opaque)
    if grow > 0:
        k = 1 + 2 * (grow * 3)
        dark = dark.filter(ImageFilter.MaxFilter(min(k, 9)))
    resL = Image.new("L", rgba.size, 255)
    resL.paste(0, mask=dark)
    resA = ImageChops.lighter(opaque, dark)
    resL = resL.resize((size, size), Image.LANCZOS)
    resA = resA.resize((size, size), Image.LANCZOS)
    return resL, resA


def paste_piece(img, symbol, box, scale=0.96, grow=1):
    x0, y0, x1, y1 = box
    side = int(min(x1 - x0, y1 - y0) * scale)
    if side <= 0:
        return
    L, A = bold_piece(symbol, side, grow)
    px = int(x0 + ((x1 - x0) - side) / 2)
    py = int(y0 + ((y1 - y0) - side) / 2)
    img.paste(L, (px, py), A)


def draw_san(img, draw, x, y, san, white, font, glyph_px):
    if not san:
        return 0
    cx = x
    rest = san
    if san[0] in "KQRBNP" and not san.startswith("O-O"):
        sym = san[0].upper() if white else san[0].lower()
        paste_piece(img, sym, (cx, y, cx + glyph_px, y + glyph_px))
        cx += glyph_px
        rest = san[1:]
    if rest:
        ty = y + max(0, (glyph_px - font.size) // 2)
        draw.text((cx, ty), rest, fill=0, font=font)
        cx += text_w(draw, rest, font)
    return cx - x


def san_w(draw, san, font, glyph_px):
    if not san:
        return 0
    w = 0
    rest = san
    if san[0] in "KQRBNP" and not san.startswith("O-O"):
        w += glyph_px
        rest = san[1:]
    if rest:
        w += text_w(draw, rest, font)
    return w


# ---- panels --------------------------------------------------------------
def notch_panel(img, draw, box, label, radius=12, dashed=False, bw=3):
    x0, y0, x1, y1 = box
    if dashed:
        _dashed_rect(draw, box, width=2)
    else:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=0, width=bw)
    if label:
        lw = text_w(draw, label, f_tiny)
        lx = x0 + 16
        draw.rectangle([lx - 5, y0 - 10, lx + lw + 7, y0 + 10], fill=255)
        draw.text((lx, y0 - 9), label, fill=0, font=f_tiny)


def _draw_flag(draw, cx, cy, color=255):
    top, bot = cy - 12, cy + 12
    draw.line([cx - 8, top, cx - 8, bot], fill=color, width=3)
    draw.polygon([(cx - 8, top), (cx + 10, top + 6), (cx - 8, top + 12)], fill=color)


def _dashed_rect(draw, box, dash=7, width=2):
    x0, y0, x1, y1 = box
    for x in range(int(x0), int(x1), dash * 2):
        draw.line([x, y0, min(x + dash, x1), y0], fill=0, width=width)
        draw.line([x, y1, min(x + dash, x1), y1], fill=0, width=width)
    for yy in range(int(y0), int(y1), dash * 2):
        draw.line([x0, yy, x0, min(yy + dash, y1)], fill=0, width=width)
        draw.line([x1, yy, x1, min(yy + dash, y1)], fill=0, width=width)


def _lab(t):
    return (t[0] if isinstance(t, (list, tuple)) else str(t)) if t else ""


def render_bold(view):
    sq = 40
    board_px = sq * 8
    bx0 = 28
    by0 = 78

    # 1) gray-fill layer -> FS dither
    base = Image.new("L", (W, H), 255)
    bdraw = ImageDraw.Draw(base)
    for (c, r, symbol, dark) in view["squares"]:
        if dark:
            x0 = bx0 + c * sq
            y0 = by0 + r * sq
            bdraw.rectangle([x0, y0, x0 + sq, y0 + sq], fill=DARK_GRAY)
    img = base.convert("1").convert("L")
    draw = ImageDraw.Draw(img)

    # 2) header (solid black, white bold text)
    draw.rectangle([0, 0, W, HEADER_H], fill=0)
    draw.text((12, HEADER_H // 2 - 10), "◀  ▶", fill=255, font=f_mid)
    title = view.get("title", "")
    tw = text_w(draw, title, f_large)
    draw.text((W // 2 - tw // 2, (HEADER_H - 28) // 2), title, fill=255, font=f_large)
    b = view.get("buttons", [])
    pills = [_lab(b[8]) if len(b) > 8 else "", _lab(b[9]) if len(b) > 9 else ""]
    pw, ph = 64, HEADER_H - 14
    px = W - 12 - pw
    for lab in reversed(pills):
        if not lab.strip():
            px -= pw + 8
            continue
        draw.rounded_rectangle([px, 7, px + pw, 7 + ph], radius=8, outline=255, width=3)
        if any(ord(ch) > 0x2000 for ch in lab):
            _draw_flag(draw, px + pw // 2, 7 + ph // 2)
        else:
            lw = text_w(draw, lab, f_small)
            draw.text((px + pw // 2 - lw // 2, 7 + ph // 2 - 9), lab, fill=255, font=f_small)
        px -= pw + 8

    # 3) board frame + labels + pieces
    draw.rectangle([bx0 - 3, by0 - 3, bx0 + board_px + 3, by0 + board_px + 3], outline=0, width=4)
    rl = view.get("rank_labels", [])
    fl = view.get("file_labels", [])
    for i in range(8):
        if i < len(rl):
            draw.text((bx0 - 22, by0 + i * sq + sq // 2 - 8), str(rl[i]), fill=0, font=f_tiny)
            draw.text((bx0 + board_px + 9, by0 + i * sq + sq // 2 - 8), str(rl[i]), fill=0, font=f_tiny)
        if i < len(fl):
            draw.text((bx0 + i * sq + sq // 2 - 5, by0 - 22), str(fl[i]), fill=0, font=f_tiny)
            draw.text((bx0 + i * sq + sq // 2 - 5, by0 + board_px + 6), str(fl[i]), fill=0, font=f_tiny)
    for (c, r, symbol, dark) in view["squares"]:
        if symbol:
            x0 = bx0 + c * sq
            y0 = by0 + r * sq
            paste_piece(img, symbol, (x0, y0, x0 + sq, y0 + sq), scale=0.96)
    for (c, r) in view.get("last_move", []):
        x0 = bx0 + c * sq
        y0 = by0 + r * sq
        draw.rectangle([x0 + 2, y0 + 2, x0 + sq - 2, y0 + sq - 2], outline=0, width=3)
    lk = view.get("loser_king")
    if lk:
        c, r = lk
        x0 = bx0 + c * sq
        y0 = by0 + r * sq
        draw.line([x0 + 5, y0 + 5, x0 + sq - 5, y0 + sq - 5], fill=0, width=3)
        draw.line([x0 + sq - 5, y0 + 5, x0 + 5, y0 + sq - 5], fill=0, width=3)

    # 4) right panel
    px0 = bx0 + board_px + 30
    px1 = W - 12
    gpx = 24

    ay0 = 66
    notch_panel(img, draw, (px0, ay0, px1, ay0 + 38), view.get("adversary_name", ""))
    cx = px0 + 14
    for (letter, white) in view.get("adversary_captured", []):
        if cx + gpx > px1 - 44:
            break
        paste_piece(img, letter.upper() if white else letter.lower(), (cx, ay0 + 7, cx + gpx, ay0 + 7 + gpx))
        cx += gpx - 7
    if view.get("adversary_adv"):
        draw.text((cx + 6, ay0 + 10), view["adversary_adv"], fill=0, font=f_small)

    my0 = ay0 + 50
    my1 = my0 + 176
    notch_panel(img, draw, (px0, my0, px1, my1), "Últimas Jogadas")
    y = my0 + 12
    for (num, wsan, bsan) in view.get("moves", [])[-6:]:
        draw.text((px0 + 12, y + 3), f"{num}.", fill=0, font=f_small)
        draw_san(img, draw, px0 + 48, y, wsan or "", True, f_small, gpx)
        draw_san(img, draw, px0 + 146, y, bsan or "", False, f_small, gpx)
        y += gpx + 3

    uy0 = my1 + 12
    notch_panel(img, draw, (px0, uy0, px1, uy0 + 38), view.get("user_name", ""))
    cx = px0 + 14
    for (letter, white) in view.get("user_captured", []):
        if cx + gpx > px1 - 44:
            break
        paste_piece(img, letter.upper() if white else letter.lower(), (cx, uy0 + 7, cx + gpx, uy0 + 7 + gpx))
        cx += gpx - 7
    if view.get("user_adv"):
        draw.text((cx + 6, uy0 + 10), view["user_adv"], fill=0, font=f_small)

    pv0 = uy0 + 50
    pv1 = H - FOOTER_H - 10
    notch_panel(img, draw, (px0, pv0, px1, pv1), view.get("move_title", ""), dashed=True)
    cxc = (px0 + px1) // 2
    lines = view.get("preview_lines")
    if lines:
        ly = pv0 + 16
        for ln in lines[:2]:
            lw = text_w(draw, ln, f_mid)
            draw.text((cxc - lw // 2, ly), ln, fill=0, font=f_mid)
            ly += 22
    else:
        w = san_w(draw, view.get("move_preview", "") or "", f_large, 28)
        draw_san(img, draw, cxc - w // 2, pv0 + 18, view.get("move_preview", "") or "",
                 view.get("preview_white", True), f_large, 28)

    # 5) footer (solid black, white dividers + bold white number + white piece)
    fy = H - FOOTER_H
    draw.rectangle([0, fy, W, H], fill=0)
    slot = W // 8
    for i in range(8):
        x0 = i * slot
        if i > 0:
            draw.line([x0, fy + 5, x0, H - 5], fill=255, width=2)
        token = b[i] if i < len(b) else None
        label = _lab(token)
        is_move = (token[2] if isinstance(token, (list, tuple)) and len(token) > 2 else True)
        draw.text((x0 + 7, fy + 5), str(i + 1), fill=255, font=f_tiny)
        if not (label and label.strip()):
            continue
        cxs = x0 + slot // 2
        if is_move and label[0] in "KQRBNP" and not label.startswith("O-O"):
            gp = 30
            paste_piece(img, label[0].upper(), (cxs - gp // 2 - 8, fy + 8, cxs - gp // 2 - 8 + gp, fy + 8 + gp))
            rest = label[1:]
            if rest:
                draw.text((cxs - gp // 2 - 8 + gp, fy + 16), rest, fill=255, font=f_small)
        else:
            lw = text_w(draw, label[:12], f_small)
            draw.text((cxs - lw // 2, fy + 15), label[:12], fill=255, font=f_small)

    bw = img.point(lambda v: 255 if v >= 128 else 0, mode="L")
    out = io.BytesIO()
    bw.convert("1").save(out, format="PNG")
    return out.getvalue()


if __name__ == "__main__":
    db = SessionLocal()
    r = RendererService()
    cases = {
        "bold_started": "fd32cc95-384b-4791-bef2-fd768e4af1fb",
        "bold_mate": "fa272811-bb5f-45b9-93bb-62ee08d56678",
    }
    for name, gid in cases.items():
        g = db.get(Game, gid)
        view = r._build_play_view(g, "meunome", db)
        data = render_bold(view)
        im = Image.open(io.BytesIO(data))
        pure = all(c in (0, 255) for _, c in im.convert("L").getcolors())
        open(f"/app/output/{name}.png", "wb").write(data)
        print(name, "bytes", len(data), "pureBW", pure)
