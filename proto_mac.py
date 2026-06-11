"""Prototype: Chess Player 2150 sprites + Chessmaster-2000-style perspective board
+ classic Mac chrome (Chicago/Geneva), rendered to pure 1bpp B/W.

Standalone iteration harness — NOT wired into PilEngine yet. Reuses the real
RendererService._build_play_view() data-prep against live DB games.

Run:  docker exec elastic_buck bash -lc 'cd /app && PYTHONPATH=/app/src python3 proto_mac.py'
"""
import os
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
A = "/app/src/hlss/assets/cp2150"
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
OUT = "/app/output"

# ---------------------------------------------------------------- fonts
def _f(name, size):
    return ImageFont.truetype(os.path.join(A, name), size)

F_TITLE = _f("ChicagoFLF.ttf", 20)     # scalable -> thresholded
F_BOX   = _f("Geneva_15.dfont", 15)    # bitmap, crisp at 15
F_SMALL = _f("Geneva_12.dfont", 12)    # bitmap, crisp at 12
F_COORD = _f("Geneva_12.dfont", 12)
F_GLYPH = ImageFont.truetype(DEJAVU, 16)
F_BTN   = _f("Geneva_15.dfont", 15)

# ---------------------------------------------------------------- sprites
SYM2FILE = {
    "P": "white_pawn", "N": "white_knight", "B": "white_bishop",
    "R": "white_rook", "Q": "white_queen", "K": "white_king",
    "p": "black_pawn", "n": "black_knight", "b": "black_bishop",
    "r": "black_rook", "q": "black_queen", "k": "black_king",
}

def _alpha_from_bg(im):
    """Derive an alpha mask by flood-filling the border-connected background
    (near-white) to transparent, preserving interior white highlights."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    marker = (255, 0, 255)
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
             (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for s in seeds:
        if sum(rgb.getpixel(s)) > 600:   # only flood from a light corner
            ImageDraw.floodfill(rgb, s, marker, thresh=60)
    px = rgb.load()
    a = Image.new("L", (w, h), 255)
    ap = a.load()
    for y in range(h):
        for x in range(w):
            if px[x, y] == marker:
                ap[x, y] = 0
    return a

@lru_cache(maxsize=None)
def sprite(sym):
    """Return (L_image, alpha_mask) for a piece symbol, NEVER resized."""
    im = Image.open(os.path.join(A, SYM2FILE[sym] + ".png")).convert("RGBA")
    a = im.split()[3]
    if a.getextrema() == (255, 255):     # opaque -> derive bg transparency
        a = _alpha_from_bg(im)
    L = im.convert("L")
    a = a.point(lambda v: 255 if v > 40 else 0)
    return L, a

# Unicode chess glyphs for captured pieces
_GORD = {"K": 0, "Q": 1, "R": 2, "B": 3, "N": 4, "P": 5}
def cap_glyph(letter, white):
    return chr((0x2654 if white else 0x265A) + _GORD[letter])

# ---------------------------------------------------------------- board geometry
# One-point perspective trapezoid, fully parameterised (Chessmaster-2000 style).
# Stronger taper (small W_FAR) opens triangular side margins for chrome.
CX        = 382     # board centre x
Y_FAR     = 132     # screen y of the far (rank-8 / top) edge of the surface
Y_NEAR    = 398     # screen y of the near (rank-1 / front) edge of the surface
W_FAR     = 300     # far-edge width
W_NEAR    = 632     # near-edge width
ROW_RATIO = 1.13    # rank foreshortening (front ranks taller); 1.0 == orthographic
SIDE_H    = 14      # 3D front-lip thickness
PIECE_LIFT = 6      # base sits this far below square centre

def _row_ys():
    hs = [ROW_RATIO ** i for i in range(8)]   # i=dr: 0 far(small) .. 7 near(big)
    scale = (Y_NEAR - Y_FAR) / sum(hs)
    ys, acc = [Y_FAR], Y_FAR
    for h in hs:
        acc += h * scale
        ys.append(acc)
    return ys

_YS = _row_ys()

def _edges_at(y):
    s = (y - Y_FAR) / (Y_NEAR - Y_FAR)
    half = (W_FAR + (W_NEAR - W_FAR) * s) / 2.0
    return CX - half, CX + half

def square_quad(dc, dr):
    yt, yb = _YS[dr], _YS[dr + 1]
    lt, rt = _edges_at(yt)
    lb, rb = _edges_at(yb)
    xTL = lt + dc / 8 * (rt - lt); xTR = lt + (dc + 1) / 8 * (rt - lt)
    xBL = lb + dc / 8 * (rb - lb); xBR = lb + (dc + 1) / 8 * (rb - lb)
    return [(xTL, yt), (xTR, yt), (xBR, yb), (xBL, yb)]

def square_anchor(dc, dr):
    q = square_quad(dc, dr)
    cx = sum(p[0] for p in q) / 4.0
    cy = sum(p[1] for p in q) / 4.0
    return cx, cy + PIECE_LIFT                     # square centre

# 50% Mac "gray" pattern, screen-aligned and deterministic
@lru_cache(maxsize=1)
def _gray_pattern():
    p = Image.new("L", (W, H), 255)
    px = p.load()
    for y in range(H):
        for x in range(W):
            if (x + y) & 1:
                px[x, y] = 0
    return p

def _poly_mask(quad):
    m = Image.new("1", (W, H), 0)
    ImageDraw.Draw(m).polygon([(round(x), round(y)) for x, y in quad], fill=1)
    return m

# ---------------------------------------------------------------- chrome helpers
def mac_box(draw, box, shadow=True, width=2):
    x0, y0, x1, y1 = box
    if shadow:
        draw.rectangle([x0 + 3, y0 + 3, x1 + 3, y1 + 3], fill=0)
    draw.rectangle([x0, y0, x1, y1], fill=255, outline=0, width=width)

def text_w(draw, s, font):
    b = draw.textbbox((0, 0), s, font=font)
    return b[2] - b[0]

def fit(draw, s, font, max_w):
    if text_w(draw, s, font) <= max_w:
        return s
    while s and text_w(draw, s + "…", font) > max_w:
        s = s[:-1]
    return s + "…"

def ctext(draw, cx, y, s, font):
    draw.text((cx - text_w(draw, s, font) / 2, y), s, font=font, fill=0)

def draw_flag(draw, x, y):
    """Small pennant flag (DejaVu has no ⚐)."""
    draw.line([(x, y), (x, y + 16)], fill=0, width=2)
    draw.polygon([(x + 2, y), (x + 13, y + 4), (x + 2, y + 8)], fill=0)

def mac_button(draw, box, index, label):
    """Mac-box styled command button: drop-shadow frame, index + centred label."""
    x0, y0, x1, y1 = box
    empty = (not label or not label.strip())
    mac_box(draw, box, shadow=not empty, width=(1 if empty else 2))
    draw.text((x0 + 4, y0 + 2), str(index), font=F_SMALL, fill=0)
    if empty:
        return
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    if any(ord(ch) > 0x2000 for ch in label):       # flag / non-ascii icon
        draw_flag(draw, int(cx - 6), int(cy - 8))
        return
    bb = draw.textbbox((0, 0), label, font=F_BTN)
    draw.text((cx - (bb[2] - bb[0]) / 2, cy - 4), label, font=F_BTN, fill=0)

# ---------------------------------------------------------------- main render
def render(view):
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)
    gray = _gray_pattern()

    sq = {(dc, dr): (sym, dark) for (dc, dr, sym, dark) in view["squares"]}

    # ---- board surface: dark squares dithered, light squares white ----
    for dr in range(8):
        for dc in range(8):
            _sym, dark = sq[(dc, dr)]
            if dark:
                img.paste(gray, (0, 0), _poly_mask(square_quad(dc, dr)))

    # ---- grid + outer frame ----
    for dr in range(9):
        y = _YS[dr]
        l, r = _edges_at(y)
        draw.line([(l, y), (r, y)], fill=0, width=1)
    for c in range(9):
        top = _edges_at(Y_FAR); bot = _edges_at(Y_NEAR)
        xt = top[0] + c / 8 * (top[1] - top[0])
        xb = bot[0] + c / 8 * (bot[1] - bot[0])
        draw.line([(xt, Y_FAR), (xb, Y_NEAR)], fill=0, width=1)
    # outer frame thicker
    fl, fr = _edges_at(Y_FAR); nl, nr = _edges_at(Y_NEAR)
    draw.line([(fl, Y_FAR), (fr, Y_FAR)], fill=0, width=3)
    draw.line([(nl, Y_NEAR), (nr, Y_NEAR)], fill=0, width=3)
    draw.line([(fl, Y_FAR), (nl, Y_NEAR)], fill=0, width=3)
    draw.line([(fr, Y_FAR), (nr, Y_NEAR)], fill=0, width=3)

    # ---- 3D front lip ----
    lip = [(nl, Y_NEAR), (nr, Y_NEAR), (nr, Y_NEAR + SIDE_H), (nl, Y_NEAR + SIDE_H)]
    img.paste(gray, (0, 0), _poly_mask(lip))
    draw.polygon([(round(x), round(y)) for x, y in lip], outline=0)
    draw.line([(nl, Y_NEAR + SIDE_H), (nr, Y_NEAR + SIDE_H)], fill=0, width=2)

    # ---- last-move highlight (before pieces) ----
    for (dc, dr) in view.get("last_move", []):
        draw.polygon([(round(x), round(y)) for x, y in square_quad(dc, dr)],
                     outline=0)
        q = square_quad(dc, dr)
        # bold inset
        draw.line([q[0], q[1]], fill=0, width=3)
        draw.line([q[3], q[2]], fill=0, width=3)

    # ---- pieces, painted far -> near ----
    for dr in range(8):
        for dc in range(8):
            symbol, _dark = sq[(dc, dr)]
            if not symbol:
                continue
            L, a = sprite(symbol)
            ax, ay = square_anchor(dc, dr)
            x = int(round(ax - L.width / 2))
            y = int(round(ay - L.height))        # base at square centre
            img.paste(L, (x, y), a)

    # ---- loser king X ----
    lk = view.get("loser_king")
    if lk:
        q = square_quad(lk[0], lk[1])
        draw.line([q[0], q[2]], fill=0, width=4)
        draw.line([q[1], q[3]], fill=0, width=4)

    # ---- edge coordinates ----
    for dr in range(8):
        yt, yb = _YS[dr], _YS[dr + 1]
        l, _ = _edges_at((yt + yb) / 2)
        lab = str(view["rank_labels"][dr])
        draw.text((l - 16, (yt + yb) / 2 - 7), lab, font=F_COORD, fill=0)
    yb = Y_NEAR + SIDE_H + 2
    for dc in range(8):
        q = square_quad(dc, 7)
        cx = (q[2][0] + q[3][0]) / 2
        ctext(draw, cx, yb, view["file_labels"][dc], F_COORD)

    btns = view["buttons"]

    # ---- top device-button strip (reserved for physical top buttons B9,B10) ----
    # turn/status title fills the left; B9 + B10 sit at the right (== hardware).
    mac_box(draw, (6, 4, 556, 40))
    draw.text((14, 12), fit(draw, view["title"], F_BOX, 530), font=F_BOX, fill=0)
    mac_button(draw, (564, 4, 672, 40), 9, btns[8][0])
    mac_button(draw, (680, 4, 788, 40), 10, btns[9][0])

    # ---- opponent card (left upper triangle) ----
    mac_box(draw, (6, 50, 214, 122))
    draw.text((12, 56), cap_glyph("N", not view["orientation_white"]),
              font=F_GLYPH, fill=0)
    draw.text((34, 58), fit(draw, view["adversary_name"], F_BOX, 172), font=F_BOX, fill=0)
    cap = "".join(cap_glyph(l, w) for (l, w) in view["adversary_captured"][:11])
    draw.text((12, 76), cap or "—", font=F_GLYPH, fill=0)
    last_san = _last_san(view)
    draw.text((12, 100), "Últ: " + (last_san or "—"), font=F_SMALL, fill=0)

    # ---- hint card (right upper triangle): the move-composition feedback ----
    mac_box(draw, (566, 50, 788, 134))
    mt = view.get("move_title") or ""
    if mt:
        draw.text((574, 56), fit(draw, mt, F_SMALL, 206), font=F_SMALL, fill=0)
    plines = view.get("preview_lines")
    if plines:
        for i, ln in enumerate(plines[:2]):
            draw.text((574, 74 + i * 18), fit(draw, ln, F_BOX, 206), font=F_BOX, fill=0)
    else:
        prev = view.get("move_preview") or ""
        ctext(draw, 677, 80, fit(draw, prev, F_TITLE, 206), F_TITLE)

    # ---- bottom footer: physical buttons B1..B8 only ----
    _footer(draw, btns)

    # ---- finalize to pure 1bpp ----
    return img.point(lambda v: 255 if v >= 128 else 0).convert("1")

def _last_san(view):
    for num, w, b in reversed(view.get("moves", [])):
        if b:
            return b
        if w:
            return w
    return None

def _footer(draw, buttons):
    """Bottom strip = physical buttons B1..B8 only, as Mac boxes."""
    y0, y1 = 430, 474
    n, gap = 8, 6
    bw = (W - gap * (n + 1)) / n
    for i in range(n):
        label = buttons[i][0] if isinstance(buttons[i], (list, tuple)) else str(buttons[i])
        x0 = gap + i * (bw + gap)
        mac_button(draw, (round(x0), y0, round(x0 + bw), y1), i + 1, label)

# ---------------------------------------------------------------- harness
def _build_views():
    from hlss.database import SessionLocal
    from hlss.models import Game
    from hlss.services.renderer import RendererService
    db = SessionLocal()
    rs = RendererService()
    samples = [
        ("midgame_black", "fd32cc95-384b-4791-bef2-fd768e4af1fb", "glmoritz"),
        ("mate_white",    "fa272811-bb5f-45b9-93bb-62ee08d56678", "glmoritz"),
        ("movestate_blk", "8eb0a729-dd98-4b80-b278-437c2127fbcc", "glmoritz"),
    ]
    out = []
    for tag, gid, name in samples:
        g = db.get(Game, gid)
        if not g:
            print("missing game", gid); continue
        out.append((tag, rs._build_play_view(g, name, db)))
    return out

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print("row ys:", [round(v, 1) for v in _YS])
    print("ys[1] - 98 (tallest back piece top):", round(_YS[1] - 98, 1),
          " (boxes bottom = 44)")
    for tag, view in _build_views():
        im = render(view)
        colors = {c for _, c in im.convert("L").getcolors()}
        path = f"{OUT}/mac_{tag}.png"
        im.save(path)
        print(f"{tag}: saved {path}  pureBW={colors <= {0,255}}  colors={sorted(colors)}")
