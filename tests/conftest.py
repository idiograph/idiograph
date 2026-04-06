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
