"""Render PLAY frames through the real integrated PilEngine.render_play.

Run: docker exec elastic_buck bash -lc 'cd /app && PYTHONPATH=/app/src python3 render_check.py'
"""
import io, os
from PIL import Image
from hlss.database import SessionLocal
from hlss.models import Game
from hlss.services.renderer import RendererService
from hlss.services.pil_engine import PilEngine

OUT = "/app/output"
SAMPLES = [
    ("midgame_black", "fd32cc95-384b-4791-bef2-fd768e4af1fb", "glmoritz"),
    ("mate_white",    "fa272811-bb5f-45b9-93bb-62ee08d56678", "glmoritz"),
    ("movestate_blk", "8eb0a729-dd98-4b80-b278-437c2127fbcc", "glmoritz"),
]

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    db = SessionLocal()
    rs = RendererService()
    eng = PilEngine()
    for tag, gid, name in SAMPLES:
        g = db.get(Game, gid)
        if not g:
            print("missing game", gid); continue
        view = rs._build_play_view(g, name, db)
        png = eng.render_play(view)
        im = Image.open(io.BytesIO(png))
        colors = {c for _, c in im.convert("L").getcolors()}
        path = f"{OUT}/check_{tag}.png"
        im.save(path)
        print(f"{tag}: {path}  pureBW={colors <= {0,255}}  size={im.size} mode={im.mode}")
