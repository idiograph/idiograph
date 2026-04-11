# Color Designer — Node Graph UI Specification
**Version:** 3
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
**Ports:** one output — type `color`
**Views (switchable via button strip):**
- Full — large swatch, label, hex field, picker button
- Compact — small swatch chip with label
- Data — hex value only, monospace

### Color Array
Collection node. Contains multiple colors as internal rows — not wired from external nodes.

**Data:** ordered list of (label, hex) pairs, dynamic length
**Ports:** one output — type `color_array`
**Behavior:**
- "+ New Item" button appends a new row inside the node body (swatch + hex field + label)
- Each row is a self-contained color entry — no external connections required
- Array label is editable
- "Match Schema" button — when a Schema node is present in the scene, resizes the array
  to match the Schema field count. Adds empty rows or trims from the bottom.
**Views:** Compact (chip + count), List (stacked rows with edit controls), Grid (swatch
matrix, read-only)

### Generate
**DEFERRED** — not part of MVP scope.

### Schema
The token role registry. Defines what semantic roles exist to be assigned.
Loaded from the active token JSON file.

**Data:** flat list of dot-notation token keys (e.g. `node.selected`, `edge.citation`)
**Port modes:**
- All — one output port per token role, type `token_role`
- Connected — only wired ports visible
- Ganged — single output port, type `token_dict` (full token object)
**Exposes:** field count as data — number of roles currently in the token file. Color
Array nodes can consume this to auto-size their row count.
**Behavior:**
- Rendered as a scrollable list of role names with color swatches
- Schema is loaded from file; new roles are added by editing the JSON directly

### Assign
Maps a single color to a specific token role.

**Data:** source color, target token role
**Ports:**
- Input: `color` (from Swatch)
- Input: `token_role` or `token_dict` (from Schema)
- Output: `assignment` — (role, hex) pair
**Behavior:** single explicit assignment — one color, one role. Role selection via dropdown
when receiving `token_dict`; role is fixed when receiving `token_role`.

### Array Assign
Bulk assignment node. Maps an entire Color Array to the full Schema positionally.

**Data:** color array, schema role list
**Ports:**
- Input: `color_array` (from Color Array)
- Input: `token_dict` (from Schema, Ganged mode)
- Output: one `assignment` port per role — positional mapping (row 1 → role 1, etc.)
**Behavior:**
- No manual role matching — positions are the mapping
- Array and Schema must have the same cardinality; node shows a warning if they differ
- Output port count matches Schema field count
- Use "Match Schema" on the Color Array node first to ensure alignment
**Port display:** All by default — each assignment is visible and traceable

### Color Correct
**DEFERRED** — not part of MVP scope.

### Filter
**DEFERRED** — not part of MVP scope.

### Write
File output node. Writes the assembled token set to a JSON file.

**Data:** output path (configurable, defaults to tokens.seed.json)
**Ports:** one or more inputs — type `assignment`
**Port display:** Ganged by default
**Behavior:**
- Writes on explicit Save button trigger — never automatically
- Uses token_store.py — no reimplementation of file writing
- Preserves existing tokens not covered by wired assignments
- Saves only what is wired to it — no scene scanning

### Drive
**DEFERRED** — not part of MVP scope. Architecture is designed and documented;
implementation follows after Idiograph demo is complete.

---

## Port Type Vocabulary

Every port has a declared type. Connections are only valid between compatible types.

| Type | Description | Produced by |
|---|---|---|
| `color` | Single hex value | Color Swatch output |
| `color_array` | Ordered list of (label, hex) pairs | Color Array output |
| `token_role` | Single role name string | Schema port (All mode) |
| `token_dict` | Full token object | Schema port (Gang mode) |
| `assignment` | (role, hex) pair | Assign or Array Assign output |

**Compatibility rules:**
- Exact type match — always valid
- `token_dict` → `token_role` input — valid; Assign extracts role via dropdown
- `color_array` → Array Assign color input — valid
- `token_dict` → Array Assign schema input — valid (Gang mode required)
- All other mismatches — invalid; rejected visually during drag

---

## Wire System (Phase F)

### Interaction
- Drag from an output port to create a wire; drop on a compatible input port to connect
- Dragging onto an incompatible port renders the wire muted/red — visual rejection signal
- Dropping on empty canvas cancels the connection
- Click a wire to select it; Delete key removes it
- Dragging from a connected input port disconnects and re-routes

### Visual
- Wires render as cubic bezier curves
- Wire color: `edge.default` from token file at rest; `edge.selected` when selected
- In-progress wire renders from port to cursor while dragging
- Port dots reflect connection state — filled when connected, hollow when empty

### Architecture
- Each port declares its type explicitly
- Wire validity is checked at connection time against the compatibility rules above
- WriteNode traverses actual wired edges — no scene scanning
- The graph is the source of truth; collect_assignments() scene scan is removed

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

View state persists per node instance. It is not a global setting.

---

## Port Display Modes

Nodes with multiple ports of the same type support three display modes, toggled via the
strip:

- **All** — every port visible
- **Connected** — only wired ports visible; unconnected ports hidden
- **Ganged** — single port representing all, emits/accepts aggregate type

Port display mode is independent of body view mode.

---

## Canvas

- Dark surface (`surface.canvas` token)
- Pan: middle mouse / space + drag
- Zoom: scroll wheel, centered on cursor
- Box select: left drag on empty canvas
- Node move: left drag on node header
- **F** — frame all nodes
- **S** — frame selected nodes (falls back to frame all if nothing selected)
- Connect: drag from output port to input port
- Disconnect: drag from connected input port to empty canvas

Port color matches edge color for the connection type. Edges carry semantic load; nodes
stay near-neutral.

---

## Token File Format

Unchanged from initial implementation. Nested JSON, underscore-separated group names:

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

## SSE Architecture — DEFERRED

FastAPI server exposes two endpoints:

- `POST /tokens` — receives full token object; broadcasts to all SSE subscribers
- `GET /events` — SSE stream; clients hold this connection open
- `POST /focus` — receives `{ "role": "..." }`; broadcasts `token.focus` to subscribers

Broadcast model: full token object sent on every update. No diffs. Receivers replace
state wholesale.

---

## What token_store.py Provides (Unchanged)

- Load JSON → flat dot-notation dict
- Set individual key
- Save flat dict → nested JSON
- No UI dependency — pure data layer

This module survives any UI rewrite unchanged.

---

## Implementation Phases

| Phase | Scope | Status |
|---|---|---|
| A | Canvas scaffold — pan, zoom, node drag, frame hotkeys | Complete |
| B | Color Swatch node — Full/Compact/Data views, view switching | Complete |
| C | Color Array node — internal rows, Grid/List/Compact views, viewport spawn | Complete |
| D | Schema node — token file integration, port display modes, node type headers | Complete |
| E | Assign node + Write node — pipeline to file output | Complete |
| F | Wire system — typed ports, bezier curves, connection validation | Complete |
| G | Array Assign node + Schema cardinality + Color Array "Match Schema" | Next |
| H | FastAPI + SSE + Drive node | Deferred |
| I | Generate, Color Correct, Filter nodes | Deferred |

---

## What Is Explicitly Deferred

- Generate, Color Correct, Filter nodes (Phase I)
- Drive node and all FastAPI/SSE work (Phase H)
- Cross-app highlight with Idiograph preview
- Merge logic for Assign override of ArrayAssign (same role, individual wins)
- Palette file format and management UI
- Color Array tabbed panel view
- Contrast checking / WCAG ratios
- CSS custom property export target
- Undo/redo
- Node graph minimap

---

## Files

```
tools/color-designer/
  pyproject.toml
  tokens.seed.json          ← seed token file
  SPEC.md                   ← this file
  test_token_store.py       ← token store round-trip test
  src/
    token_store.py          ← pure data layer, no UI dependency
    main.py                 ← entry point, canvas setup, seed nodes
    canvas.py               ← QGraphicsScene/View, pan/zoom/select
    nodes/
      base_node.py          ← shared chrome, port system, view switching
      swatch_node.py        ← Phase B
      array_node.py         ← Phase C
      schema_node.py        ← Phase D
      assign_node.py        ← Phase E
      write_node.py         ← Phase E
      array_assign_node.py  ← Phase G
```

---

*Companion documents: session-2026-04-09.md, session-2026-04-08.md, demo_design_spec-1.md*
