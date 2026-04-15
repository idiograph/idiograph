# Color Designer — Node Graph UI Specification
**Version:** 1
**Status:** LIVING — subject to revision during development
**Date:** April 9, 2026

---

## Overview

Color Designer is a standalone PySide6 desktop application for designing and iterating
interface color palettes. It is implemented as a node graph — not a flat token list. The
graph architecture is not decorative: it is a direct expression of the same thesis that
drives Idiograph. Color design is a pipeline. The tool makes that pipeline explicit,
inspectable, and auditable.

The tool outputs a named semantic token file (JSON). It can also broadcast live token
updates to a FastAPI SSE endpoint, driving a connected D3 demo interface without page
reload.

Color Designer is currently housed in `tools/color-designer/` within the Idiograph repo.
It is built to be extractable into a standalone tool after the Idiograph demo is complete.
No Idiograph-specific logic belongs inside the tool's core.

---

## Architectural Principle

The node graph is the source of truth. The token file is an output. The SSE broadcast
is an output. The schema is an input constraint. Nothing is hardcoded — node types,
token roles, and view representations are all data-driven.

This mirrors Idiograph's own architecture: the graph is authoritative, interfaces are
declared projections.

---

## Node Types

### Color Swatch
Single color input. The atomic unit of the system.

**Data:** one hex value, one label
**Ports:** one output (color)
**Views (switchable via button strip):**
- Full — large swatch, label, hex field, picker button
- Compact — small swatch chip with label
- Data — hex value only, monospace

### Color Array
Collection node. Contains multiple colors as internal rows — not wired from external nodes.

**Data:** ordered list of (label, hex) pairs, dynamic length
**Ports:** one output (color array)
**Behavior:**
- "+ New Item" button appends a new row inside the node body (swatch + hex field + label)
- Each row is a self-contained color entry — no external connections required
- Array label is editable
**Views:** stacked list (only view — tabs deferred)

### Generate
**DEFERRED** — not part of MVP scope.

### Schema
The token role registry. Defines what semantic roles exist to be assigned.
Loaded from the active token JSON file.

**Data:** flat list of dot-notation token keys (e.g. `node.selected`, `edge.citation`)
**Ports:** multiple outputs — one per token role
**Behavior:**
- Rendered as a scrollable list of role names
- Selecting a role broadcasts a `token.focus` event (see SSE section)
- Schema is loaded from file; new roles are added by editing the JSON directly

### Assign
Maps a color or array slot to a specific token role.

**Data:** source (color or array slot), target (token role from Schema)
**Ports:** one color input; one token output (role + value pair)
**Behavior:** the explicit assignment step — this is where semantic meaning is attached

### Color Correct
**DEFERRED** — not part of MVP scope.

### Filter
**DEFERRED** — not part of MVP scope.

### Write
File output node. Writes the assembled token set to a JSON file.

**Data:** output path
**Ports:** accepts one or more token inputs (role + value pairs)
**Behavior:** writes on explicit trigger (button), not automatically

### Drive
**DEFERRED** — not part of MVP scope. Architecture is designed and documented; implementation follows after Idiograph demo is complete.

---

## Cross-App Highlight (Live Inspection) — DEFERRED

Designed and documented. Not part of MVP scope. Implementation follows after Idiograph
demo is complete and the FastAPI/SSE layer is built.

When implemented: selecting a token role in the Schema node broadcasts a `token.focus`
event; the Idiograph preview highlights the corresponding UI element.

Two SSE event types defined:
- `token.update` — `{ "role": "node.selected", "value": "#7eb8f7" }` — color changed
- `token.focus` — `{ "role": "node.selected" }` — role currently selected/active

---

## Node View Switching

Every node has a button strip at the bottom edge. Pressing a button cycles the node
to a different view representation. View is a declared projection of the node's data —
the data does not change, only the visual encoding.

Button strip icons encode view type visually (not just labels).

View state persists per node instance. It is not a global setting.

---

## Canvas

Standard node graph canvas:

- Dark surface (inherits `surface.canvas` token — the tool designs its own surface)
- Pan: middle mouse / space + drag
- Zoom: scroll wheel
- Box select: left drag on empty canvas
- Node move: left drag on node header
- Connect: drag from output port to input port
- Disconnect: drag from connected port to empty canvas

Port color matches edge color for the connection type — consistent with Idiograph's
color philosophy. Edges carry semantic load; nodes stay near-neutral.

---

## Palette Management

A palette is a named configuration — it points to a token JSON file and stores the
node graph layout (node positions, connections, view states).

Palette files are separate from token files:
- `my-palette.cdpalette` — JSON, stores graph state + path to token file
- `tokens.seed.json` — the token output, consumed by Idiograph and other targets

This separation means the token file can be consumed directly by other systems without
knowledge of the graph that produced it.

---

## Token File Format

Unchanged from current implementation. Nested JSON, underscore-separated group names:

```json
{
  "surface": { "canvas": "#1a1a1f", "panel": "#24242c" },
  "node": { "default": "#2e2e3a", "selected": "#7eb8f7" },
  "node_status": { "pending": "#555568", "running": "#f7c948" },
  ...
}
```

The token file is open — new roles are added by editing JSON directly. The Schema node
regenerates its port list on reload.

---

## SSE Architecture

FastAPI server exposes two endpoints:

- `POST /tokens` — receives full token object; broadcasts to all SSE subscribers
- `GET /events` — SSE stream; clients hold this connection open
- `POST /focus` — receives `{ "role": "..." }`; broadcasts `token.focus` to subscribers

Color Designer posts to `/tokens` on Drive node trigger.
Color Designer posts to `/focus` on Schema role selection.
Idiograph preview (D3) holds open `/events` connection.

Broadcast model: full token object sent on every update. No diffs. Receivers replace
state wholesale.

---

## What token_store.py Provides (Unchanged)

- Load JSON → flat dot-notation dict
- Set individual key
- Save flat dict → nested JSON
- No UI dependency — pure data layer

This module survives the UI rewrite unchanged.

---

## Implementation Phases — MVP

**Phase A — Canvas scaffold**
PySide6 QGraphicsScene/QGraphicsView canvas with pan, zoom, empty node drag.
No node logic yet. Prove the canvas works.

**Phase B — Color Swatch node**
First complete node type. Full / Compact / Data views. Picker and hex field functional.
View switching via button strip.

**Phase C — Color Array node**
Collection node with internal add-item rows. Edge rendering with port color.
Connect Swatch output to Array input for mixed workflows.

**Phase D — Schema node + token file integration**
Load token JSON. Render role list as scrollable output port list.

**Phase E — Assign node + Write node**
Complete the pipeline to file output. End-to-end: Swatch → Assign → Write → JSON.
Tool is useful and demonstrable at this phase.

---

## Deferred Phases (Post-Idiograph Demo)

**Phase F — FastAPI + SSE + Drive node**
Live broadcast pipeline. Full cross-app highlight with Idiograph preview.

**Phase G — Generate, Color Correct, Filter nodes**
Generative and refinement stages.

---

## What Is Explicitly Deferred

- Generate, Color Correct, Filter nodes (Phase G)
- Drive node and all FastAPI/SSE work (Phase F)
- Cross-app highlight with Idiograph preview
- Palette file format and management UI
- Color Array tabbed panel view
- Contrast checking / WCAG ratios
- CSS custom property export target
- Undo/redo
- Node graph minimap

---

## Files — MVP

```
tools/color-designer/
  pyproject.toml
  tokens.seed.json          ← seed token file (unchanged)
  src/
    token_store.py          ← unchanged, pure data layer
    main.py                 ← SCRAPPED, replaced by canvas entry point
    canvas.py               ← QGraphicsScene/View (Phase A)
    nodes/
      base_node.py          ← shared node chrome, port system, view switching
      swatch_node.py        ← Phase B
      array_node.py         ← Phase C
      schema_node.py        ← Phase D
      assign_node.py        ← Phase E
      write_node.py         ← Phase E
```

---

*Companion documents: session-2026-04-09.md, session-2026-04-08.md, demo_design_spec-1.md*
