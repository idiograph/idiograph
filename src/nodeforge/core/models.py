from typing import Any 
from typing import Literal
from pydantic import BaseModel, Field


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

class Edge(BaseModel):
    source: str = Field(description="ID of the source node.")
    target: str = Field(description="ID of the target node.")
    type: str = Field(
        default="DATA",
        description="Edge type defining the relationship. Known types: DATA (passes values), CONTROL (gates execution). Extensible — additional semantic types such as MODULATES or DRIVES are valid."
    )


class Graph(BaseModel):
    name: str = Field(description="Human-readable name for this graph.")
    version: str = Field(description="Version string for this graph definition.")
    nodes: list[Node] = Field(default_factory=list, description="All nodes in the graph.")
    edges: list[Edge] = Field(default_factory=list, description="All edges in the graph.")

    def get_node(self, node_id: str) -> Node | None:
        """Return a node by id, or None if not found."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None