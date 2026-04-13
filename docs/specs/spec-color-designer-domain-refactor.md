# spec-color-designer-domain-refactor.md
**Status:** LIVING
**Freeze trigger:** Merge of `refactor/color-designer-domain` to `main` complete, 44-test gate passing
**Created:** 2026-04-12

---

## Problem

Color Designer lives in `tools/color-designer/` as a standalone tool with its own `uv`
environment. The only reason it was separated was the PySide6 dependency. That separation
means:

- Color Designer is not an Idiograph domain — it does not run through the executor
- The Qt node classes own execution logic that belongs in handler functions
- No demonstration that Idiograph is domain-agnostic beyond arXiv
- Two environments to maintain

The fix is to fold Color Designer into the main repo as a proper Idiograph domain, using
optional extras to keep PySide6 out of the arXiv environment.

---

## Goal

Two independent apps, one repo, one shared core:

- `idiograph.apps.arxiv_server` — FastAPI/D3, no Qt dependency
- `idiograph.apps.color_designer` — Qt app, no arXiv dependency
- Both register only their own handlers at startup
- Both can run simultaneously as separate OS processes
- The SSE Drive node connection between them is HTTP — no shared process or memory

---

## Target Structure

> **Note:** Spec originally used aspirational flat paths (handlers/arxiv.py, pipelines/arxiv.py).
> Corrected in Step 4 to reflect actual AMD-011 layout: domains/<domain>/handlers.py.

```
src/idiograph/
  core/                              ← unchanged — pure Python, no domain deps
  domains/
    arxiv/
      handlers.py                    ← add register_arxiv_handlers() at bottom (Step 4 ✓)
      __init__.py                    ← existing register_all() — demote to test convenience (Step 6)
    color_designer/
      __init__.py                    ← new: register_color_designer_handlers()
      handlers.py                    ← new: token pipeline handler implementations
      pipeline.py                    ← new: color pipeline as Idiograph Graph
  apps/
    __init__.py                      ← empty (Step 1 ✓)
    arxiv_server.py                  ← FastAPI entry point (Phase H — not yet built)
    color_designer/
      __init__.py                    ← empty (Step 1 ✓)
      main.py                        ← Qt entry point, registers color_designer_handlers
      canvas.py
      token_store.py                 ← moved from tools/color-designer/src/ (Step 2 ✓)
      tokens.seed.json               ← moved from tools/color-designer/ (Step 2 ✓)
      SPEC.md                        ← moved from tools/color-designer/ (Step 2 ✓)
      nodes/
        __init__.py
        base_node.py
        swatch_node.py
        array_node.py
        schema_node.py
        assign_node.py
        write_node.py
        array_assign_node.py
```

---

## Dependency Isolation

**`pyproject.toml` addition:**

```toml
[project.optional-dependencies]
qt = ["pyside6"]
```

**Running arXiv server — Qt not installed:**
```powershell
uv run python -m idiograph.apps.arxiv_server
```

**Running Color Designer — Qt installed:**
```powershell
uv run --extra qt python -m idiograph.apps.color_designer.main
```

PySide6 is never imported outside `apps/color_designer/`. The arXiv server has no
knowledge of Qt.

---

## Handler Registration Pattern

Each handler module exposes its own explicit registration function. No shared
`register_all()` in production entry points.

```python
# domains/arxiv/handlers.py — added at bottom (Step 4 ✓)
def register_arxiv_handlers() -> None:
    register_handler("FetchAbstract", fetch_abstract)
    register_handler("LLMCall", llm_call)
    # ... remaining arxiv handlers

# domains/color_designer/handlers.py — new file
def register_color_designer_handlers() -> None:
    register_handler("swatch", handle_swatch)
    register_handler("assign", handle_assign)
    register_handler("write_tokens", handle_write_tokens)
    # ... remaining color designer handlers
```

**Entry point boot sequence:**

```python
# apps/color_designer/main.py
from idiograph.domains.color_designer import register_color_designer_handlers
register_color_designer_handlers()
# Qt app init follows

# main.py CLI (arXiv)
from idiograph.domains.arxiv.handlers import register_arxiv_handlers
register_arxiv_handlers()
# pipeline execution follows
```

`register_all()` in `domains/arxiv/__init__.py` may be retained as a test convenience
(registers all domains at once for integration tests) but must never be called from
a production entry point.

---

## Migration Steps

Test gate must pass before and after each step. Branch: `refactor/color-designer-domain`.

| Step | Action | Test gate |
|---|---|---|
| 1 | `git checkout -b refactor/color-designer-domain` | — |
| 2 | Create `apps/` package skeleton — `__init__.py` files only | Pass |
| 3 | Move `tools/color-designer/src/*` → `apps/color_designer/`, fix all imports | Pass |
| 4 | Move `tokens.seed.json` and `SPEC.md` | Pass |
| 5 | Add `register_arxiv_handlers()` to `handlers/arxiv.py` | Pass |
| 6 | Update `main.py` CLI to call `register_arxiv_handlers()` explicitly | Pass |
| 7 | Remove or demote `register_all()` in `handlers/__init__.py` | Pass |
| 8 | Add `qt = ["pyside6"]` optional extra to `pyproject.toml` | Pass |
| 9 | Delete `tools/color-designer/` | Pass |
| 10 | Verify Qt app launches: `uv run --extra qt python -m idiograph.apps.color_designer.main` | Manual |
| 11 | Merge to `main` | Pass |

---

## What This Does Not Include

The following are out of scope for this refactor and come after the merge:

- `handlers/color_designer.py` handler implementations (new domain handlers)
- `pipelines/color_designer.py` graph definition
- Qt canvas refactor to hand off execution to the Idiograph executor
- Phase H: FastAPI, SSE, Drive node

The migration moves existing code into the correct structure. The domain integration
(canvas → executor handoff) is a subsequent task, scoped separately.

---

## Git Workflow

This refactor introduces feature branch discipline:

- `main` stays clean — always passing tests, always runnable
- One branch per significant refactor or new domain
- Merge when 44-test gate passes and feature is demonstrably working
- No PRs, no review ceremony
- Preferred merge: `git merge refactor/color-designer-domain` with a descriptive commit message

Recommended commit message on merge:
```
refactor: fold color designer into idiograph as apps/color_designer domain

- Moves tools/color-designer into src/idiograph/apps/color_designer
- Adds pyside6 as optional extra [qt]
- Introduces explicit per-domain handler registration
- Removes register_all() from production entry points
- No behavior change to arXiv pipeline or executor
```

---

*Companion documents: spec-phase-09-plan.md, SPEC.md (color designer), blueprint_amendments_3.md*
