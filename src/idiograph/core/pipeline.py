# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from idiograph.core.models import Node, Edge, Graph

SAMPLE_PIPELINE: Graph = Graph(
    name="lookdev_approval_pipeline",
    version="1.0",
    nodes=[
        Node(id="node_01", type="LoadAsset", params={"asset_path": "/assets/hero_character.usd"}),
        Node(id="node_02", type="ApplyShader", params={"shader": "principled_bsdf", "material": "hero_skin"}),
        Node(id="node_03", type="ShaderValidate", params={"rules": ["energy_conservation", "normal_range"]}),
        Node(id="node_04", type="RenderComparison", params={"reference": "/refs/hero_approved.exr", "renderer": "arnold"}),
        Node(id="node_05", type="LookApproval", params={"approver": "lead_lookdev", "threshold": 0.95}),
    ],
    edges=[
        Edge(source="node_01", target="node_02", type="DATA"),
        Edge(source="node_02", target="node_03", type="DATA"),
        Edge(source="node_03", target="node_04", type="CONTROL"),
        Edge(source="node_04", target="node_05", type="DATA"),
    ],
)
