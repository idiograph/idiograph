# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import pytest
from idiograph.core.models import Node, Edge, Graph


@pytest.fixture
def sample_graph() -> Graph:
    return Graph(
        name="test_pipeline",
        version="1.0",
        nodes=[
            Node(id="n1", type="LoadAsset",     params={"path": "/test.usd"}),
            Node(id="n2", type="ApplyShader",   params={"shader": "pbr"}),
            Node(id="n3", type="ShaderValidate", params={}),
            Node(id="n4", type="LookApproval",  params={"threshold": 0.9}),
        ],
        edges=[
            Edge(source="n1", target="n2", type="DATA"),
            Edge(source="n2", target="n3", type="DATA"),
            Edge(source="n3", target="n4", type="CONTROL"),
        ],
    )


@pytest.fixture
def branching_graph() -> Graph:
    """
    A branching graph whose longest chain is NOT any shortest source→sink path.

    `sample_graph` is a straight line, where longest and shortest coincide and
    a shortest-path implementation of `critical_path` looks correct. This one
    separates them:

        root ─┬─> alpha ─┐
              ├─> beta  ─┼─> merge ─┬─> tail ─> leaf
              └──────────┘          └─> quick

    `root → merge` is the shortcut: it skips alpha/beta entirely. The two sinks
    are `leaf` (4 hops the short way, 5 nodes the long way) and `quick`. Taking
    shortest paths gives ['root', 'merge', 'tail', 'leaf'] — 4 nodes; the true
    longest chain is 5, through alpha. `alpha` and `beta` are interchangeable
    by length, so the fixture also pins the tie-break.
    """
    return Graph(
        name="branching_test",
        version="1.0",
        nodes=[
            Node(id="root",  type="LLMCall",        params={}),
            Node(id="alpha", type="VectorRetrieve", params={}),
            Node(id="beta",  type="VectorRetrieve", params={}),
            Node(id="merge", type="ToolInvoke",     params={}),
            Node(id="tail",  type="Evaluator",      params={}),
            Node(id="leaf",  type="MemoryUpdate",   params={}),
            Node(id="quick", type="MemoryUpdate",   params={}),
        ],
        edges=[
            Edge(source="root",  target="alpha", type="DATA"),
            Edge(source="root",  target="beta",  type="DATA"),
            Edge(source="root",  target="merge", type="DATA"),  # shortcut
            Edge(source="alpha", target="merge", type="DATA"),
            Edge(source="beta",  target="merge", type="DATA"),
            Edge(source="merge", target="tail",  type="DATA"),
            Edge(source="merge", target="quick", type="DATA"),
            Edge(source="tail",  target="leaf",  type="DATA"),
        ],
    )


@pytest.fixture
def cyclic_graph() -> Graph:
    return Graph(
        name="cyclic_test",
        version="1.0",
        nodes=[
            Node(id="a", type="LLMCall", params={}),
            Node(id="b", type="Evaluator", params={}),
        ],
        edges=[
            Edge(source="a", target="b", type="DATA"),
            Edge(source="b", target="a", type="CONTROL"),  # cycle
        ],
    )
