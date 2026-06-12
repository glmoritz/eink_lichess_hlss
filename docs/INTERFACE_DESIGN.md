# Interface Design — the e‑ink chess device

This is the design system for every screen the device shows, on **both** sides of
the wire:

- **HLSS** (this repo) renders the *application* screens server‑side as whole 1‑bit
  images (PLAY, NEW MATCH, SETUP, …) — see `src/hlss/services/pil_engine.py`.
- **Firmware** (`hello_eink`, the ESP32‑S3) renders the *device‑local* screens with
  LVGL (boot log, network, clock, alarm) and owns the physical buttons — see
  `src/device_ui.c`.

Read this before adding or changing a screen. The goal is that a user can't tell
which side drew a given screen: same fonts, same chrome, same button grid.

---

## 1. Why it looks the way it does

The panel is an **800×480, 1‑bit black/white e‑paper** (4‑gray hardware, but we treat
output as pure B/W; the LLSS Floyd–Steinberg pass is the identity on binary pixels).
Thin anti‑aliased UI toolkits look cheap and muddy on a panel like this. Two earlier
attempts (a hairline "wireframe" look, then a heavier "bold e‑ink" look) were both
rejected as cheap.

The chosen direction is the **golden‑age black‑and‑white Macintosh UI** — Chicago/Geneva
type, white windows with hard 1‑px borders and a hard (un‑blurred) drop shadow, a
deterministic 50 % gray desktop. It was *designed* for 1‑bit screens, so it reads
crisply with zero anti‑aliasing. The reference is **Sargon III for Macintosh**
(`sargoniii.jpeg`): a clean menu bar, white windows with a distinct title bar, a
dithered desktop. The chess PLAY board borrows the **Chess Player 2150** (Atari ST)
3‑D sprites on a **Chessmaster‑2000** one‑point‑perspective board — see
`project_eink_bold_renderer` and the `render_play` docstring.

**Hard rules**

- Output is **pure 1‑bit B/W**. No grays except *deterministic* dither patterns.
- **Never anti‑alias type.** Bitmap fonts at native size; bpp 1 on the firmware side.
- **Never resize the piece sprites** — any scale destroys the hand‑pixeling.

---

## 2. Type

Two families, period. Sargon III itself uses only two (Chicago 12 + Geneva 9, a gentle
~1.3:1 ratio). That is enough; resist adding a third family.

- **Chicago** — all chrome: title bars, menu/heading text, and short emphasis values.
- **Geneva** — everything else: body copy, labels, soft‑key captions, dense lists.

### Cuts in use

| Side | Variable | Font @ size | Used for |
|------|----------|-------------|----------|
| HLSS (PIL) | `f_mac_title` | Chicago (ChicagoFLF.ttf) @ 20 | title bar, dialog headline, big values |
| HLSS (PIL) | `f_mac`       | Geneva 15 (Geneva_15.dfont) @ 15 | body, labels |
| HLSS (PIL) | `f_mac_small` | Geneva 12 (Geneva_12.dfont) @ 12 | captions, footer numbers, dense text |
| Firmware (LVGL) | `chicago_18` | Chicago @ 18 | title bar + emphasis values |
| Firmware (LVGL) | `geneva_14`  | Geneva @ 14 | body, labels, soft‑keys |
| Firmware (LVGL) | `geneva_9`   | Geneva @ 9  | log console (dense) |
| Firmware (LVGL) | `dseg_bold_italic_200` | DSEG7 @ 200 | the big "fat LCD" clock display |
| Firmware (LVGL) | `material_design_40 / _120` | Material Symbols | wifi/temp/humidity/status icons |
| Firmware (LVGL) | `lv_font_montserrat_14` | — | **symbol glyphs only** (`LV_SYMBOL_DOWN`, `LV_SYMBOL_OK`); not body text |

Chicago is a scalable outline, so any px is fine. The firmware standardised on **18**
(was 22) to free vertical room. `chicago_22 / geneva_12` were trialed and dropped;
`chicago_18 / geneva_9` are spare cuts kept for dense layouts.

### Font sources (GitHub)

- **Chicago**: `JohnDDuncanIII/macfonts` → `ChicagoFLF.ttf` (the scalable Chicago HLSS uses),
  or a pixel Chicago (`pixChicago`) for the firmware bitmap cut.
- **Geneva** and the Espy alternates: `jcs/classic-mac-fonts` → `ttf/Geneva-9/12/14.ttf`,
  `ttf/EspySansBold-14.ttf`.

### Language

The UI is **Portuguese (pt-BR)**, end to end — the primary user is a young child who
isn't fluent in English. Write every user-facing string in Portuguese.

**Diacritics split by side — this bit us, so read carefully:**
- **HLSS (PIL fonts)**: ChicagoFLF/Geneva are full fonts that **do** carry the accented
  glyphs, so write proper diacritics: `Configuração`, `endereço`, `botão`, `nível`.
- **Firmware (LVGL cuts)**: the bitmap cuts actually shipped (`chicago_18` from pixChicago,
  `geneva_14`/`geneva_9` from the Geneva trace) were ranged `0x20-0x7E, 0xA0-0xFF` but the
  source faces **have no glyphs for the Latin-1 accents**, so every `á/ã/ç/é/í/ó/õ/ú` rendered
  as a `.notdef` **box** on the panel. Firmware copy is therefore **plain ASCII, no
  diacritics** (`Relogio`, `Sabado`, `Marco`, `conexao`, `Aguardando horario`). If real
  accents are ever needed on the device, source/convert a font cut that genuinely contains
  the Latin-1 letters (e.g. a Geneva/Espy cut from `jcs/classic-mac-fonts`) and re-range it.
- **Em-dash `—`**: never in firmware strings (the cuts lack `0x2014` too) — use a comma or
  `-`. HLSS PIL fonts do carry it. Diagnostic `LOG_*` console lines (not on the panel) may
  stay English with em-dashes.

### Converting a font to LVGL (firmware side)

Use the online converter <https://lvgl.io/tools/fontconverter> (or `lv_font_conv`):

- **Bpp = 1** (no anti‑aliasing → crisp + deterministic, matches the panel).
- **Range** = `0x20-0x7E, 0xA0-0xFF, 0x2014` — printable ASCII + the Latin‑1 supplement
  (covers all Portuguese accents `À..ÿ`, `ª º`) + the em‑dash `—`. Don't drop the em‑dash
  on a Chicago cut; the `—` placeholders are drawn in Chicago.
- **The bitmap‑font trap:** a *traced bitmap* TTF (Geneva‑14, pixChicago, …) is only crisp
  at its **native pixel size** (the number in the filename) or an integer multiple. Render
  it at any other size and at bpp 1 the strokes thin out to broken hairlines. (This is
  exactly how a wrong `depixelklein @12` produced an unreadable "Geneva" — the fix was
  the real `Geneva‑14.ttf @ 14`.) Scalable outlines (ChicagoFLF) are exempt.
- Keep the LVGL **Name** stable across a re‑cut (e.g. `geneva_14`) so only the `.c` file
  changes — the `CMakeLists.txt` source line and `LV_FONT_DECLARE` stay put.

---

## 3. Chrome components

- **`mac_box`** — the atom. White fill, **2‑px black border**, and a **hard 3‑px black
  drop shadow** (a solid offset rectangle, *not* a blur — blurs dither badly on EPD).
  HLSS: `_mac_box(draw, box)`. Firmware: `mac_box(parent, x, y, w, h)` returns the white
  box; add children to it.
- **Top bar** — the one *distinct* bar (Sargon's title bar). A full‑width `mac_box` with a
  **down‑chevron + separator at the far‑left** (the device‑menu affordance, see §4) and a
  Chicago title. HLSS: `_top_bar`. Firmware: `build_header`. This is the only prominent
  bar on a screen; every other box stays plain/unintrusive.
- **Footer / soft‑keys** — an 8‑cell strip on a **light deterministic‑gray band** carrying
  white `mac_box` key‑caps with Geneva labels, one cell per physical bottom button. Empty
  cells show the bare band. **Two‑line labels are supported and encouraged** where a caption
  needs them (firmware uses `LV_LABEL_LONG_WRAP`; keep it). HLSS: `_play_footer` /
  `_btn_cell`. Firmware: `build_softkeys`.
- **Cards / dialogs** — plain `mac_box`es: Chicago headline, Geneva sub‑text, a 1‑px divider
  for sections. The opponent card, the new‑game dialog, the setup dialog are all this.

### Layout metrics

| | Width | Header zone | Footer zone | Margin | Button grid |
|-|-------|-------------|-------------|--------|-------------|
| Firmware | 800 | **64** px | **72** px | — | 8 cells, `cw = 100`, gap 8 |
| HLSS | 800 | 50 px | 50 px | 10 | 8 cells, gap 6 (`_btn_cell`) |

> **Known reconciliation item:** the two sides don't yet share identical header/footer
> heights or cell math. They render in different contexts (the firmware footer only shows
> on device‑local screens; HLSS draws its own footer into the frame), so pixel‑identical
> bars aren't strictly required — but the **8 cell centers should line up under the 8
> physical buttons** on both. If you touch either grid, converge `cw`/gap so a key‑cap
> sits over its button on both sides. Firmware bar heights (64/72) are fixed — don't change
> them.

---

## 4. Button contract  *(consolidated — this is the source of truth)*

The device has **16 buttons: a top row of 8 and a bottom row of 8**, each row aligned to
the 8 on‑screen cells above/below it. (Today's hardware still uses 4 direct‑GPIO keys for
the top row + debug; the contract below is the product target that all screen design
should assume.)

### Normal operation (no overlay)

- **Bottom row (8)** → **always the application's.** These are your context/action keys;
  label them in the footer (`_play_footer`). Forwarded to the active HLSS app.
- **Top row, leftmost key** → **device‑local, always.** Never forwarded to the app. It is
  the **device‑menu toggle** and the *only* key the application may not use.
- **Top row, other 7 keys** → **the application's**, same as the bottom row. Use them.

### Pressing the leftmost top key → overlay ON

The firmware **device‑local overlay** appears and **takes over the entire top row and the
whole screen region** (boot log / network / clock / alarm). While the overlay is up, the
application sees none of the top row; the overlay owns navigation and actions.

### Pressing the leftmost top key again → overlay OFF

The overlay hides and the top row's other keys **return to the application**.

### What screen designers must do

- Draw the **down‑chevron affordance at the far‑left of the top bar** (`_top_bar` does this)
  so the user always knows the leftmost key opens the device menu. Never put an app action
  on the leftmost top cell.
- You **may** use the other 7 top keys and all 8 bottom keys. Assume any of them can be
  *temporarily* taken away while the device overlay is open, and restored when it closes —
  design actions to be stateless across that, not mid‑gesture.
- Align labels to **physical cell positions** (use `_btn_cell`), never as free‑floating
  hint text. (The old `"<  >  screen   ESC exit"` header hint was removed precisely because
  it didn't sit over its buttons.)

### Note on the older model

Earlier drafts said the menu hovered over only the *first four* top cells (`btn9..btn12`)
and opened on a long‑press. The consolidated rule above **supersedes** that: it's the whole
top row + screen, toggled by a single press of the leftmost key. The chevron's *position*
(far‑left) is unchanged; only its meaning grew.

---

## 5. The story so far  *(how we got here)*

1. **Wireframe** PLAY screen — too thin/cheap on 1‑bit. Rejected.
2. **"Bold e‑ink"** rewrite — heavier, still hand‑rolled. Rejected as cheap.
3. **Vintage‑Mac** direction adopted: reuse real golden‑age 1‑bit art instead of inventing a
   look. Chess Player 2150 sprites + Chessmaster perspective board + Chicago/Geneva. PLAY
   built first.
4. **NEW MATCH** revamped to match (Mac dialog, Stockfish‑AI‑in‑the‑opponent‑list, create on
   BTN_5/ENTER) — see `project_new_match_flow`.
5. **Firmware** device screens restyled to the same Mac chrome; Montserrat body fonts dropped
   in favor of Chicago/Geneva converted to LVGL. Geneva mis‑conversion (`depixelklein`) caught
   and fixed (`Geneva‑14`); fonts consolidated to `chicago_18 / geneva_14 / geneva_9`.
6. **Button layout consolidated** to the §4 contract.
7. **SETUP** moved onto the Mac chrome (last app screen to convert).
8. *Next / future:* a 2‑D Sargon‑III‑style board as a swappable alternate piece set; keep the
   piece loader and board projection independent so it drops in without touching layout.

---

## 6. Checklist for a new screen

- [ ] Pure 1‑bit B/W out (`finalize()` on HLSS; dither only where you mean it on firmware).
- [ ] Chicago for chrome/headlines, Geneva for everything else. No third family. No AA.
- [ ] One distinct top bar (`_top_bar`), chevron at far‑left, everything else plain.
- [ ] Leftmost top cell left free for the device menu; labels aligned to `_btn_cell`.
- [ ] Footer key‑caps over their physical buttons; two‑line labels where useful.
- [ ] Piece sprites never resized.
