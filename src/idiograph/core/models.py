# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from typing import Any, Literal
from pydantic import BaseModel, Field

class PortType(BaseModel):
    label: str = Field(description="Human-readable label for this type. Example: 'RGB Color', 'USD Stage Handle'.")
    description: str = Field(description="What data this type carries and its expected semantics.")


class PortDeclaration(BaseModel):
    name: str = Field(description="Port name as it appears on the node. Must be unique within input_ports or output_ports.")
    port_type: str = Field(description="Type identifier referencing a key in Graph.type_registry. Enforcement is post-Phase-8.")

class Node(BaseModel):
    id: str = Field(description="Unique identifier for this node within the graph.")
    type: str = Field(description="Node type determining its role. Examples: LoadAsset, Render, LLMCall, ShaderValidate.")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific parameters for this node. Keys and value types vary by node type."
    )
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"] = Field(
    default="PENDING",
    description="Execution status. PENDING → RUNNING → SUCCESS or FAILED."
    )
    input_ports: list[PortDeclaration] | None = Field(
        default=None,
        description="Declared input ports for this node. Declaring them BINDS the node: the executor builds its inputs solely from port-declared incoming edges, keyed by to_port. None leaves the node in the legacy regime, where it receives every upstream payload keyed by source node id. Empty list is a declaration — the node is bound and accepts no inputs."
    )
    output_ports: list[PortDeclaration] | None = Field(
        default=None,
        description="Declared output ports for this node. Each name is a key the handler emits in its returned dict; declaring them lets a bound consumer read from_port off this node and lets validation check dataflow without reading handler source. None means undeclared. Empty list means the node produces no outputs."
    )
    resources: list[str] | None = Field(
        default=None,
        description="Named run-supplied capabilities this node's handler requires — network clients, credentials, anything the run owns rather than the graph. The twin of input_ports and the same one-way ratchet, differing only in where the value comes from: ports pull from upstream nodes, resources pull from the run. Declaring them means the executor hands the handler a keyword-only resources mapping containing ONLY these names; a name the run did not supply raises UnsuppliedResourceError before any handler executes. None leaves the node in the legacy regime, where its handler is called with two positional arguments. Empty list is a declaration — the node declares and receives an empty mapping. Resource VALUES are supplied at execute time and never enter a content address."
    )

class Edge(BaseModel):
    source: str = Field(description="ID of the source node.")
    target: str = Field(description="ID of the target node.")
    type: str = Field(
        default="DATA",
        description="Edge type defining the relationship. Known types: DATA (passes values), CONTROL (gates execution). Extensible — additional semantic types such as MODULATES or DRIVES are valid."
    )
    from_port: str | None = Field(
        default=None,
        description="Declared output port on the source node this edge reads. Must name an entry in the source's output_ports. Set together with to_port or not at all — declaring exactly one is a validation error."
    )
    to_port: str | None = Field(
        default=None,
        description="Declared input port on the target node this edge feeds. Must name an entry in the target's input_ports. The executor binds inputs[to_port] = upstream_output[from_port]; a missing from_port key at runtime is an explicit error, never a silent skip."
    )


class Graph(BaseModel):
    name: str = Field(description="Human-readable name for this graph.")
    version: str = Field(description="Version string for this graph definition.")
    nodes: list[Node] = Field(default_factory=list, description="All nodes in the graph.")
    edges: list[Edge] = Field(default_factory=list, description="All edges in the graph.")
    type_registry: dict[str, PortType] | None = Field(
        default=None,
        description="Named type definitions available to port declarations in this graph. Keys are type identifiers referenced by PortDeclaration.port_type."
    )

    def get_node(self, node_id: str) -> Node | None:
        """Return a node by id, or None if not found."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None
