# HLSS – Lichess e-Ink Client

This repository implements a **High Level Screen Service (HLSS)** that allows users to play **Lichess correspondence and live games** using a **minimal e-Ink device** driven by the LLSS (Low Level Screen Service).

The service is a **Python microservice** that:

* integrates with the **Lichess API**,
* renders complete e-Ink user interfaces server-side,
* and exposes them to LLSS as logical frames.

All UI rendering is performed on the server using **Pillow** and **python-chess**, while the device remains a dumb terminal responsible only for input forwarding and framebuffer display.

---

## Core principles

* **Zero chess logic on the device**
* **Server-side rendering only**
* **Stateless device interaction**
* **Multiple Lichess accounts per device**
* **Optimized for slow-refresh, monochrome e-Ink displays**

---

## Initial (unconfigured) state

If no Lichess account is configured, the HLSS renders a setup screen containing:

* A **web configuration URL**
* A **QR code** pointing to that URL

Accessing the configuration page allows the user to:

* add one or more Lichess users
* store API tokens securely
* manage multiple accounts for use on the same device

---

## Web configuration interface

The web UI allows:

* Managing **multiple Lichess accounts**
* Enabling/disabling accounts
* Selecting default accounts
* Viewing active games per account
* Revoking or updating API tokens

Each configured account becomes selectable on the device.

---

## Screen navigation model

The HLSS exposes multiple logical screens.
The device switches between them using:

* **HLleft / HLright** → cycle through screens

---

## Screen 1 – New Match

A single-screen interface to create a new Lichess game using the 8 context buttons.

### Button mapping

* **BTN1** – Cycle through configured Lichess users

* **BTN2** – Choose side: White / Black / Random

* **BTN8** – Show configuration URL + QR code

* **ENTER** – Create match

* **ESC** – Cancel

The screen shows:

* selected user
* selected color
* game type (default: correspondence, configurable later)

---

## Screen 2 – Play Screen (per ongoing game)

Each ongoing match has its own Play Screen.

### Layout

* **Left side**

  * Chessboard (current position)
  * Last move highlighted
  * Arrow indicating pending move (when applicable)

* **Right side**

  * Player names
  * Captured pieces
  * Move list (last 10 moves)
  * Context-sensitive action menu

---

## Move input model (3–4 click workflow)

Moves are constructed using a **context-driven menu**, optimized for few buttons and no touchscreen.

### Step 1 – Select piece / castle

```
BTN1 = Pawn
BTN2 = Knight
BTN3 = Bishop
BTN4 = Rook
BTN5 = Queen
BTN6 = King
BTN7 = Castle (king side)
BTN8 = Castle (queen side)
```

Only valid options for the current position are enabled.

---

### Step 2 – Select destination file (A–H)

Buttons map to columns **A–H**, dynamically updated based on valid moves.

---

### Step 3 – Select destination rank (1–8)

Buttons map to valid ranks for the previously selected file.

---

### Step 4 – Optional disambiguation

If multiple pieces can reach the same square:

* an extra selection step resolves ambiguity

---

### Move confirmation

Once a move is fully formed:

* The board updates optimistically
* An arrow highlights the move (from → to)

**ENTER** → Confirm move
**ESC** → Cancel move

After move confirmation:

* **ENTER** context changes to *Offer Draw*
* **ESC** context changes to *Resign* (with confirmation)

---

## Rendering and chess logic

* **python-chess**

  * Board state
  * Legal move generation
  * SAN/UCI handling
  * Disambiguation logic

* **Pillow**

  * Board rendering
  * Piece glyphs
  * UI elements
  * QR codes
  * Monochrome optimization for e-Ink

All output is rendered as PNG frames and submitted to LLSS.

---

## Suggested extensions (planned / optional)

* Clock display for timed games
* Premoves for correspondence
* Game filtering (only games where it’s your turn)
* Soundless notifications via forced refresh
* Endgame tablebase hints (optional, server-side only)
* Board orientation toggle per game

---

## Integration with LLSS

This HLSS:

* submits rendered frames to LLSS
* receives input events from LLSS
* notifies LLSS on game state changes (opponent move, game end)

It strictly follows the LLSS OpenAPI contract and does not interact directly with devices.

---

## Target use case

This project is designed for:

* correspondence chess players
* low-distraction environments
* ultra-low-power e-Ink terminals
* users who want chess without screens, ads, or notifications

---

This HLSS is intentionally opinionated and optimized for clarity, reliability, and long-term maintainability.
