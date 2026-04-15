# Rename Summary — Idiograph → Idiograph

## What Changed
The project was renamed from Idiograph to Idiograph across every layer:
source directory, package name, CLI entry point, all internal imports, config
file, logging namespace, and GitHub repository. The rename is complete and
permanent. 44 tests pass without modification. No functionality changed.

## Why It Changed
Idiograph named the mechanism — nodes, building — without naming the idea.
The new name carries the thesis directly.

**Idiograph** is a real English word (first recorded 1623) meaning "a mark or
signature peculiar to an individual" — from the Greek idio (one's own) +
graphos (written). Dictionary.com lists a secondary definition: "a private mark
or trademark." The name rewards unpacking: an idiograph is authenticated,
specific, and reproducible — exactly what the system produces.

The full expansion is **an idiomatic semantic graph** — a graph that speaks
the language of its domain correctly, not approximately. This is the play on
words the name is built on: idiomatic (domain-native, correct from the inside)
compressed into a coined proper noun that contains "graph" completely.

The name also carries the idiographic/nomothetic distinction from Windelband:
idiographic inquiry deals with the concrete and individual rather than universal
distributions. A probabilistic system is nomothetic by design. A deterministic
semantic graph is idiographic — this specific pipeline, these specific nodes,
this specific execution. The particular is the point.

Idiograph sounded like a product a Foundry competitor would ship. Idiograph
sounds like a thesis.

## Namespace Secured
- Domain: `theidiograph.com` (registered via Porkbun)
- GitHub org: `github.com/idiograph`
- Repo: `github.com/idiograph/idiograph`
- USPTO TESS: no live marks found for "idiograph" in Class 9 or Class 42
- `.com` aftermarket only — `theidiograph.com` secured as primary domain

## Changes Made

### Directory
```
src/idiograph/  →  src/idiograph/
```

### pyproject.toml
```toml
# Before
name = "idiograph"
idiograph = "idiograph.main:app"
packages = ["src/idiograph"]

# After
name = "idiograph"
idiograph = "idiograph.main:app"
packages = ["src/idiograph"]
```

### Config file
```
idiograph.toml  →  idiograph.toml
[idiograph]     →  [idiograph]
```

### All internal imports
```python
# Before
from idiograph.core import ...
from idiograph.handlers import ...
from idiograph.pipelines import ...

# After
from idiograph.core import ...
from idiograph.handlers import ...
from idiograph.pipelines import ...
```

### Logging namespace
```python
# Before
logging.getLogger("idiograph")
logging.getLogger(f"idiograph.{name}")

# After
logging.getLogger("idiograph")
logging.getLogger(f"idiograph.{name}")
```

### GitHub
- Repo renamed from Idiograph to idiograph
- Repo transferred to the idiograph org
- Local remote updated:
  `git remote set-url origin https://github.com/idiograph/idiograph.git`

## Verified Working
```
uv pip install -e .               → installs as idiograph
uv run idiograph --help           → all subcommands listed under idiograph
uv run pytest tests/ -v           → 44 passed, 0 failed
git push                          → repo live at github.com/idiograph/idiograph
```

## Commit
```
git add -A
git commit -m "Rename: idiograph → idiograph"
git push
```

## Note on Project Directory
The working directory remains `E:\projects\nodeforge` for now. Renaming the
folder is optional and low priority — the package name, CLI, imports, and
GitHub all reflect the new name. Rename the folder when convenient, not as a
gate on anything.

## Next
Essay rebuild. The literacy-to-architecture argument with Idiograph as the
landing point. Five concepts, five production illustrations, one architectural
conclusion, one clear ask.
