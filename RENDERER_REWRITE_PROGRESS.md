# Renderer rewrite + dither/depth contract — working doc & progress

**Living doc. Update the Status checklist + Progress log as work proceeds so this can be
resumed cold.** Approved plan: `~/.claude/plans/now-that-we-are-piped-cray.md`.

## Goal (why)

1. Replace HLSS's HTML-template + headless-Chrome (Playwright/CDP `ws://localhost:3000`)
   rendering with a **self-contained, color-aware PIL renderer**. Output frames already
   correct for the **1bpp** panel: crisp solid-black text/pieces over **ordered dot-mesh**
   grays — no dithering, so small fonts & chess pieces stay sharp.
2. Add a **per-frame metadata contract**: HLSS tells LLSS "already rendered to target
   palette — **don't dither**" (`skip_dither`) + `source_bit_depth`; LLSS hints the device
   color depth to HLSS via the existing `POST /init`.

Constraint: Chrome path keeps working until PIL proven on **hardware**. **PLAY (chess) is
the priority screen.** Target now = 1bpp B/W; contract carries depth for future 2bpp.

## Environment / resume facts

- Repos: HLSS `/home/guilherme/00_tmp/eink_lichess_hlss` · LLSS `/home/guilherme/00_tmp/eink_llss`
  · firmware `/home/guilherme/00_tmp/zephyr/introduction-to-zephyr/workspace/projects/hello_eink`
- Containers: HLSS=`elastic_buck` (code bind-mounted at `/app`, **no --reload → restart via VS Code after edits**),
  LLSS=`dazzling_blackwell` (uvicorn `--reload`; pkgs under **vscode** user; DB postgres),
  firmware build=`Zephyr-Introduction` (`ninja -C build-sim`, `ZEPHYR_BASE=/opt/toolchains/zephyr`).
- Run python in containers as: `docker exec -u vscode dazzling_blackwell bash -lc '... /usr/local/bin/python ...'`
  (HLSS: `docker exec elastic_buck ... python3`, files under `/app`).
- Deps already present (HLSS): pillow, cairosvg, python-chess, qrcode, playwright. No new heavy deps.

## Key decisions (locked)

- Engine = extend PIL infra in `src/hlss/services/renderer.py` (reuse `_load_fonts`,
  `_create_base_image`, `_content_bounds`, `_render_header`, `_render_context_bar`,
  `_image_to_bytes`; `render_setup_screen` is the PIL reference). Rasterize the 12 piece SVGs
  **once at init** via cairosvg → cached B/W bitmaps (white fill + black outline), composite.
- Grays = ordered **dot-mesh** (Bayer/checker → pure 0/255); text rendered to L then
  **thresholded** (no AA gray fringe). No Floyd-Steinberg in the new path.
- Backend selector: `RENDERER_BACKEND=auto|pil|chrome` (default `auto`) + per-screen map in
  `_render_frame`. PIL where ported, Chrome else. Remove Chrome/templates/Playwright only in
  Phase 4 after HW validation.
- Contract transport: `skip_dither` + `source_bit_depth` as **multipart form fields** on
  `POST /instances/{id}/frames`; stored on LLSS `Frame`; consulted in `get_frame`. Depth hint
  reuses `POST /init` (`DisplayCapabilities.bit_depth` → `Instance.display_bit_depth`).
- 1bpp no-dither path = `image.convert("1", dither=Image.Dither.NONE)` (hard threshold;
  identity for already-pure-B/W input).

## Status checklist

Legend: [ ] todo · [~] in progress · [x] done · [!] blocked

### Phase 0 — scaffolding (no behavior change)
- [x] HLSS: `renderer_backend` setting in `config.py`
- [x] HLSS: backend selector — implemented INSIDE `RendererService` (`_use_pil()`), not `_render_frame`; auto/pil→PIL, chrome→legacy HTML

### Phase 1 — dither/depth contract
- [ ] LLSS: `Frame` add `skip_dither: bool=False`, `source_bit_depth: int|null` (`app/db_models.py`) + alembic migration
- [ ] LLSS: `submit_frame` accept+store form fields (`app/routers/instances.py`; keep hash-dedup)
- [ ] LLSS: `frame_converter` honor `skip_dither` (1bpp threshold via `dither=NONE`) (`app/frame_converter.py`)
- [ ] LLSS: `get_frame` read `frame.skip_dither`, pass to converter (`app/routers/devices.py`)
- [ ] HLSS: `llss.submit_frame` send fields (`src/hlss/services/llss.py`)
- [ ] HLSS: `Frame` add `skip_dither` col (`src/hlss/models.py`) + alembic; `_submit_frame` passes it (`routers/instances.py`)
- [ ] Verify depth hint lands: `/init` → `Instance.display_bit_depth`
- [ ] TEST contract: Chrome frame still dithers (skip_dither=False); a pre-B/W frame w/ skip_dither=True → `get_frame?raw=true` is threshold-identity

### Phase 2 — PIL engine + SETUP + NEW_MATCH
- [x] primitives: `mesh_fill`, crisp thresholded text, cached piece bitmaps + `paste_piece`, mesh header/buttons
- [x] port SETUP to primitives (QR already B/W)
- [x] port NEW_MATCH (card, actions, helper, footer buttons)
- [x] selector → PIL for SETUP+NEW_MATCH (skip_dither contract dropped; pure B/W is FS-identity)
- [x] visual check (pure B/W assert)

### Phase 3 — PLAY (priority)
- [x] `render_play_screen_pil` data-prep → `view` dict; PIL `render_play` tail
- [x] board (mesh dark squares, last-move border, loser-king X, flip), pieces, rank/file labels
- [x] side panel: captured, move history (inline glyphs+SAN), move-preview
- [x] move-input buttons via `MoveSelectionHelper` per `MoveStateStep`; game-over overlay
- [x] selector → PIL for PLAY; visual validated (DB games). [ ] device/HW validation pending

### Phase 4 — cleanup (after HW validation)
- [ ] PIL default all screens; remove Chrome branch, `html_renderer.py`, `*.html`, playwright dep

## How to test (reference)
- Visual: render each screen via PIL path to `/output`, assert pixels ⊆ {0,255} at 1bpp; compare to Chrome.
- Contract byte test: submit pre-B/W frame skip_dither=True → `get_frame?raw=true` packed bytes == pre-rendered packed (no FS). Reuse pattern-test CP byte-compare style.
- E2E: HLSS restart + LLSS reload + native_sim; drive a game; crisp, no re-dither, no re-fetch churn.
- HW: flash ESP32-S3, confirm on real SSD1677.

## Risks
- Text AA gray fringe → must threshold text to pure B/W.
- cairosvg piece quality at small px (rasterize at exact square size, threshold; white fill+outline).
- LLSS hash-dedup: write skip_dither/bit_depth even when reusing a row.
- Alembic migrations both backends. HLSS needs manual restart per change.

## BLOCKER + KEY INSIGHT (2026-06-09) — resequenced

**Insight:** Floyd-Steinberg dithering is a **no-op on an already-pure-B/W image** (every
pixel already exactly 0/255 → quantization error 0 → nothing diffuses; `image.convert("1")`
returns the same pixels). So **the color-aware renderer outputting pure B/W ALREADY fixes
the ugly-dither problem through the existing LLSS path** — the explicit `skip_dither`
contract is an optimization / future-proofing (for 2bpp GRAY2 or intentional non-binary
patterns), **not required** for the 1bpp visual win.

**Blocker for the explicit contract:** LLSS DB is the user's **homelab Postgres**
(`postgres.tutu.lan:5432`, schema `eink_llss`), `frames` table owned by superuser `root`;
app connects as non-owner `eink_root` → **cannot add columns** without superuser DDL.
Alembic is also broken here (migrations hardcode wrong schema `eink`; DB built via
`create_all`, never stamped). Adding columns to the ORM model without the DB columns
**breaks every Frame query** (the live device fetch) — verified and reverted.

**Decision:** resequence — **build the renderer first** (Phase 2/3, no DB dependency,
delivers the visual fix). Treat the explicit dither/depth contract as a later phase gated on
the user running DDL (or granting `eink_root` ownership). Pending user choice (asked).

### Revised checklist delta
- Phase 1 contract → **DEFERRED** (needs homelab-DB DDL; not required for 1bpp visual fix).
- New order: Phase 0 selector → Phase 2 renderer primitives + SETUP/NEW_MATCH → Phase 3 PLAY
  → (later) Phase 1 contract → Phase 4 cleanup.

## Progress log (newest first)
- **Phase 3 PLAY done + selector wired (2026-06-09).** Added
  `RendererService.render_play_screen_pil()` (mirrors the HTML data-prep of
  `render_play_screen` but emits an HTML-agnostic `view` dict with plain SAN strings,
  display-space board coords via `to_disp`, captured-piece lists, and glyph-aware
  `(label, white, is_move)` button tokens) → `engine.render_play(view)`. Selector wired
  **inside RendererService** (surgical, `_render_frame` untouched): `_use_pil()` returns
  True for backend `auto`/`pil`; `render_setup_screen` / `render_new_match_screen` /
  `render_play_screen` dispatch to the engine at the top, legacy HTML kept as the
  `chrome` fallback. Engine tweaks: button token grew a 3rd `is_move` field so plain
  command labels ("CONFIRMAR", "CANCELAR", **"Repetir Jogo"**) are NOT glyph-rendered
  (the leading R/K/Q… would otherwise draw a piece); `render_play` now supports
  `view["preview_lines"]` for the wrapped game-over summary. Validated against real DB
  games (all **pure B/W**, mode "1" PNG ~6.4–9 KB): started+SELECT_PIECE (black orient),
  SELECT_FILE, SELECT_RANK, CONFIRM (pending move previewed on board + large glyph SAN),
  MATE (loser-king X, "Repetir Jogo" plain text), OUT_OF_TIME. Output PNGs in
  `/app/output/play_*.png`. **NOT yet restarted** — needs ONE manual VS Code restart of
  HLSS (`elastic_buck`, no --reload) to take effect; then device/HW validation.
  Phase 4 (remove Chrome/templates/playwright) still gated on HW validation.
- **Phase 2 engine validated.** New `src/hlss/services/pil_engine.py` (`PilEngine`):
  Bayer-8x8 `mesh_fill`, `_mesh_field` (cached, pure-python, no numpy), cairosvg piece
  bitmap cache (`_piece_rgba`/`paste_piece`), crisp text via final `finalize()` threshold,
  header/footer/button/panel primitives, board primitives pending. SETUP + NEW_MATCH render
  **pure B/W** (verified colors⊆{0,255}, ~3KB PNGs) and look crisp (viewed). config.py has
  `renderer_backend` setting. NOT yet wired into RendererService/_render_frame (do after PLAY,
  one HLSS restart). Test cmd: `docker exec elastic_buck bash -lc 'cd /app && PYTHONPATH=/app/src
  python3 -c "from hlss.services.pil_engine import PilEngine; ..."'` → /app/output/*.png.
- **User decision: NO DB / drop explicit contract.** Renderer-only path. The PIL renderer
  emits pure B/W; existing LLSS FS-dither is inert on it. Phase 1 contract cancelled.
  Working order now: Phase 0 selector → Phase 2 primitives+SETUP+NEW_MATCH → Phase 3 PLAY →
  Phase 4 cleanup.
- Reverted LLSS `Frame` model columns (would break live queries; DB not owned by app role).
  Removed wrong-schema 004 migration. LLSS healthy (Frame count 1386).
- (init) Doc created.
