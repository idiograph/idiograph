# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0

"""HIT leg: which registry root it replays from, and which source it names.

``scripts/demos/crispr_hit_leg.py::_warm_registry_root`` picks the root the warm
leg reads — the operator's XDG durable root when it already holds the demo
artifact, the record packaged under ``idiograph.demo`` otherwise — and returns a
label saying which source won. It shipped with no automated coverage, and the
one branch a fresh clone does NOT take is the branch a cold freeze lands on.

These tests bind all three selection cases:

1. XDG holds a record named by ``frozen_crispr_address()`` → XDG is selected, so
   an operator who froze their own artifact replays THEIR freeze.
2. XDG holds a DIFFERENT 64-hex ``.json`` → the packaged registry is selected.
   This is the case the coverage exists for: presence is keyed on the demo's ONE
   content address, not on a non-empty glob, because an operator holding some
   other artifact is — for THIS demo — a stranger. A glob would pin them to XDG
   and the warm leg would MISS.
3. XDG empty, or absent entirely → the packaged registry is selected.

The label is asserted as well as the path. It is what the operator reads on
screen to know which source won, and a silently-wrong label is exactly how the
fallback branch would masquerade as the own-freeze branch. It is asserted by
which SOURCE it names, not word for word, so a copy-edit does not go red.

All of this is offline: root selection is one glob of the packaged registry plus
one file-existence test. No ``_main()``, no ``resolve_seeds``, no network call,
no credential. ``XDG_DATA_HOME`` is monkeypatched onto ``tmp_path``, so the
operator's real durable root is never read and never written.
"""

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from idiograph.demo import REGISTRY_ROOT, frozen_crispr_address

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMOS_DIR = _REPO_ROOT / "scripts" / "demos"
_DEMO_SCRIPT = _DEMOS_DIR / "crispr_hit_leg.py"

# A plausible content address that is NOT the demo's — a well-formed 64-hex
# filename, deliberately not a junk name like ``garbage.txt``. The whole point of
# case 2 is that selection matches on the ADDRESS: a decoy that failed the shape
# check could fall through for the wrong reason and prove nothing. Its bytes are
# never read — selection only tests for the file's existence.
_DECOY_ADDRESS = (
    "9f2c4a7e1b6d8035c5e0a91d4f7b23680d8e6b13a4c95f27be14803da6f2c79e"
)


@contextmanager
def _demos_on_sys_path() -> Iterator[None]:
    """Put ``scripts/demos`` on ``sys.path`` for the duration of the load.

    ``crispr_hit_leg`` does ``from crispr_freeze_trigger import …``, relying on
    ``sys.path[0]`` being its own directory when run as a script. Loading it by
    path without that entry raises ``ModuleNotFoundError``. The entry is removed
    again afterwards rather than left in place: the alternative — an
    ``__init__.py`` under ``scripts/demos``, a pytest path setting, or a
    ``conftest.py`` hook — changes the tree's shape to suit one test, which is
    the move ``test_freeze_trigger_address.py`` declines by name.
    """
    original = list(sys.path)
    sys.path.insert(0, str(_DEMOS_DIR))
    try:
        yield
    finally:
        sys.path[:] = original


def _load_demo_module():
    """Load the HIT leg demo script by file path.

    ``scripts/demos/`` is not a package, so the module is loaded from its path
    rather than imported — the same approach as
    ``test_freeze_trigger_address.py::_load_demo_module``, extended with the
    sibling-import shim above. Safe to execute: the module level is imports,
    constants and definitions only; ``load_dotenv()`` lives inside
    ``_openalex_key()`` and ``_main()`` is guarded behind ``__main__``.
    """
    spec = importlib.util.spec_from_file_location(
        "idiograph_demo_crispr_hit_leg", _DEMO_SCRIPT
    )
    assert spec is not None and spec.loader is not None, _DEMO_SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with _demos_on_sys_path():
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    return _load_demo_module()


@pytest.fixture
def xdg_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``XDG_DATA_HOME`` at ``tmp_path`` and return the durable root.

    ``_durable_registry_root()`` reads the environment inside its body on every
    call, so setting the variable is the whole fixture — no monkeypatching of
    the function and no import-order care needed. The directory is NOT created
    here: case 3 needs it absent, and the tests that need it create it.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path / "idiograph" / "pipeline-registry"


def _place_record(root: Path, address: str) -> Path:
    """Put a record named for ``address`` in ``root``, creating it if needed.

    The content is a placeholder because selection never reads it — it tests the
    path for existence and nothing more.
    """
    root.mkdir(parents=True, exist_ok=True)
    record = root / f"{address}.json"
    record.write_text("{}", encoding="utf-8")
    return record


def test_xdg_root_holding_the_demo_record_is_selected(demo, xdg_root: Path) -> None:
    """The own-freeze branch: XDG holds the artifact, so XDG wins.

    This is the branch a cold freeze lands on and the one a fresh clone never
    exercises. If it goes red the operator's own freeze is being ignored and the
    warm leg silently replays the packaged record instead.
    """
    _place_record(xdg_root, frozen_crispr_address())

    root, label = demo._warm_registry_root()

    assert root == xdg_root, (
        "XDG holds the demo's own record but was not selected — the operator's "
        f"own freeze at {xdg_root} is being ignored in favour of {root}. Fix "
        "the selection in _warm_registry_root, not this test."
    )
    assert "xdg" in label.lower(), (
        f"XDG was selected but the label {label!r} does not name it. The label "
        "is what the operator reads to know which source won; one that does not "
        "say XDG lets the own-freeze branch read as the packaged fallback."
    )


def test_xdg_root_holding_a_different_address_falls_through(
    demo, xdg_root: Path
) -> None:
    """The stranger rule: a DIFFERENT artifact in XDG must not pin selection.

    Selection is keyed on the demo's one content address, not on a non-empty
    glob. An operator whose durable root holds some other artifact is, for THIS
    demo, a stranger: pinning them to XDG would make the warm leg MISS and
    re-derive at live OpenAlex and Anthropic cost.
    """
    assert _DECOY_ADDRESS != frozen_crispr_address(), (
        "the decoy address collides with the demo's real address, so this test "
        "would assert nothing — pick a different 64-hex value for _DECOY_ADDRESS"
    )
    _place_record(xdg_root, _DECOY_ADDRESS)

    root, label = demo._warm_registry_root()

    assert root == REGISTRY_ROOT, (
        "an XDG root holding a DIFFERENT address was selected — selection is "
        "matching on presence rather than on the demo's address, so a stranger "
        "with any artifact at all gets pinned to XDG and the warm leg MISSES"
    )
    assert "packaged" in label.lower(), (
        f"the packaged registry was selected but the label {label!r} does not "
        "name it as the packaged fallback, so a fallback would read on screen "
        "as the operator's own freeze"
    )


def test_empty_xdg_root_falls_through(demo, xdg_root: Path) -> None:
    """An XDG root that exists but holds nothing falls through to the package."""
    xdg_root.mkdir(parents=True)

    root, label = demo._warm_registry_root()

    assert root == REGISTRY_ROOT, (
        f"an empty XDG root at {xdg_root} was selected — the warm leg would "
        "find no artifact and fail on a root that holds nothing, when the "
        "packaged record was available all along"
    )
    assert "packaged" in label.lower(), (
        f"the packaged registry was selected but the label {label!r} does not "
        "name it as the packaged fallback"
    )


def test_absent_xdg_root_falls_through(demo, xdg_root: Path) -> None:
    """A missing XDG root is the fresh-clone case and must not raise.

    Distinct from the empty case: nothing here has ever created the directory,
    so selection has to tolerate a path that is not there at all rather than
    only one that is there and empty.
    """
    assert not xdg_root.exists(), xdg_root

    root, label = demo._warm_registry_root()

    assert root == REGISTRY_ROOT, (
        f"an absent XDG root at {xdg_root} was selected — a fresh clone would "
        "replay from a directory that does not exist instead of from the record "
        "packaged in the wheel"
    )
    assert "packaged" in label.lower(), (
        f"the packaged registry was selected but the label {label!r} does not "
        "name it as the packaged fallback"
    )
