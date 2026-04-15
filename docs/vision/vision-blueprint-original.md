VFX Idiograph – Semantic Graph System for VFX and AI Workflows (2026 Edition)
Full Curriculum Blueprint (Final Integrated Version + Strategic Layer)

0. Project Overview
0.1 Purpose
Build a single evolving Python project (“VFX Idiograph”) that progresses from:
structured CLI tools
 → typed data models
 → semantic graph system
 → async orchestration system
 → AI/agent-operable pipeline
The system is deliberately hybrid: it supports both traditional VFX production pipelines and modern AI agent workflows under the exact same architecture.
This is not just a learning project — it is a proof-of-concept system that supports a broader thesis about how AI systems should operate in production environments.

0.2 End State
A system that:
represents VFX pipelines (asset loading, rendering, simulation, compositing, look dev) and AI agent workflows (LLM calls, tool invocation, evaluation, memory) as a unified semantic graph
is fully JSON-serializable
is typed and validated
supports CLI interaction (UI optional and secondary)
exposes deterministic tool interfaces
can be read, modified, debugged, and optimized by an LLM or autonomous agent

0.3 Core Architectural Principle
The semantic graph is the single source of truth.
 UI, CLI, and agents are all views or operators on that graph.
The same Node/Edge structure powers:
procedural VFX pipelines
look development workflows
dynamic agentic systems

0.4 Primary Goal (Strategic Framing)
Demonstrate a new model for AI-operable production systems using a deterministic, semantically structured graph.
This project exists to:
prove that production pipelines require explicit structure, not probabilistic inference
show how VFX-style graph systems solve problems AI systems currently struggle with
provide a working implementation that validates the thesis

1. Global System Concepts
1.1 Semantic Graph Model
Every node follows a consistent schema:
Node
id: str
type: str
params: dict (structured, validated — evolves into discriminated unions per node type)
status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"]
inputs: list (optional)
outputs: list (optional)
Node Domains
VFX Nodes
LoadAsset
Render
Simulate
ApplyShader
Cache
Composite
Look Development Nodes (First-Class Domain)
MaterialAssign
ShaderValidate
LookApproval
RenderComparison
AI Nodes
LLMCall
VectorRetrieve
ToolInvoke
Evaluator
Router
MemoryUpdate
HumanInLoop

Edge
connects nodes
explicit type:
DATA (passes values/assets)
CONTROL (defines execution flow)

Graph
collection of nodes + edges
fully JSON-serializable
reconstructable from JSON
always validatable

1.2 System Layers
Data Layer


dataclasses / Pydantic models
Logic Layer


graph operations (create, connect, validate)
Interface Layer


CLI (Typer)
UI (Qt / PySide6 — optional, delayed)
Orchestration Layer


async execution
subprocess / tool calls
dependency resolution (topological sort)
Agent Layer


tool interfaces
structured inputs/outputs
deterministic graph mutation

1.3 Execution Model
The graph represents declarative state
Execution layer performs side effects
Nodes execute via topological ordering
DATA edges pass values
CONTROL edges define triggers
Failure Handling
dependent nodes halt
independent branches continue
Key Constraint
The graph itself never executes code directly — it only describes execution.

2. Global Standards
2.1 Tooling
Python 3.14
uv (project + dependency management)
ruff (format + lint)
VS Code (format-on-save)
2.2 Coding Standards
Type hints: introduced early, strict later
JSON-first data thinking
Small, composable functions
No UI state as source of truth
Graph must always be serializable and validatable
2.3 Project Rules
One repository only
src/idiograph/ layout
Every phase extends existing code
Every session produces a runnable result

3. Phase Structure
Each phase contains:
Goal
Thesis
What We Build (micro-sessions)
AI / Forward-Thinking Angle
Wrap-Up

4. Curriculum Phases

Phase 0 – Environment & Tooling Lock-in
Goal
 Create a working Idiograph project with CLI entry point.
Thesis
 Remove friction early to focus on system design.
Micro-Sessions
install uv
create repo
define pyproject
add CLI stub
AI Angle
 Package is immediately usable as an agent tool.
Wrap-Up
 Production-grade Python environment established.

Phase 1 – Rapid Fluency & Semantic Output
Goal
 Produce structured JSON instead of print output.
Thesis
 Structured data enables both pipelines and AI.
Artifacts
pipeline manifest JSON
CLI commands (idiograph stats, idiograph workflows)
Micro-Sessions
structured dicts
JSON I/O
CLI wrapping
AI Angle
 Machine-readable outputs enable agent reasoning.
Wrap-Up
 Scripts become semantic data producers.

Phase 2 – Project Structure & Reusability
Goal
 Convert scripts into a reusable package.
Thesis
 Reusable systems require modular structure.
Artifacts
idiograph.core
installable package
Micro-Sessions
module refactor
local install
AI Angle
 Functions become callable tools.
Wrap-Up
 System becomes modular.

Phase 3 – Data Models & Typing
Goal
 Replace dicts with validated models.
Thesis
 Explicit schemas enable reliability and AI safety.
Artifacts
Node, Edge, Graph models
validation commands
Micro-Sessions
define models
validate graph
integrate CLI
AI Angle
 Agents can safely read/write system state.
Wrap-Up
 System gains enforceable structure.

Phase 4 – Interface Layer (Optional / Delayed UI)
Goal
 Provide optional human interaction layer.
Thesis
 UI is not core — the graph is.
Artifacts
CLI-first workflow
optional Qt graph viewer
Micro-Sessions
minimal UI (if pursued)
AI Angle
 Agents and CLI operate on same graph.
Wrap-Up
 UI is explicitly secondary.

Phase 4.5 – Graph Query & Analysis (Critical Phase)
Goal
 Enable deep inspection and reasoning over the graph.
Thesis
 A graph is only valuable if it can be interrogated.
Key Concepts
networkx
traversal
query DSL
hybrid queries
Artifacts
QueryEngine
CLI query command
Example Queries
downstream Render nodes from LLMCall
FAILED nodes
cycle detection
subgraph extraction
Micro-Sessions
integrate networkx
traversal logic
query builder
CLI integration
AI Angle
 Agents can debug and optimize before execution.
Wrap-Up
 System gains observability and reasoning.

Phase 5 – Testing, Logging, Config
Goal
 Ensure system reliability.
Thesis
 Production systems must be testable and observable.
Artifacts
tests
logging
config
Micro-Sessions
pytest
logging setup
config loading
AI Angle
 Prevents agent corruption of state.
Wrap-Up
 System becomes stable.

Phase 6 – Async & Orchestration
Goal
 Execute the graph.
Thesis
 Execution is structured coordination, not scripting.
Artifacts
execution engine
hybrid pipeline simulation
Micro-Sessions
async execution
dependency ordering
progress tracking
Rules
topological sort
partial failure handling
AI Angle
 Agents trigger real operations.
Wrap-Up
 Graph becomes executable.

Phase 7 – Architecture Refinement
Goal
 Improve clarity and extensibility.
Thesis
 Architecture emerges through iteration.
Artifacts
refactored system
Micro-Sessions
modular cleanup
separation of concerns
Future Target
Command pattern (if needed)
AI Angle
 Cleaner system improves agent manipulation.
Wrap-Up
 System becomes maintainable.

Phase 8 – Agent Integration (Accelerated Priority)
Goal
 Expose Idiograph as deterministic tools for agents.
Thesis
 Agents operate on structured systems, not prompts.
Artifacts
tool interfaces
graph mutation API
Micro-Sessions
define tools
integrate with LLM frameworks
agent modifies graph
Agent Capabilities
create
inspect
debug
repair
optimize
AI Angle
 This is the core proof of the thesis.
Wrap-Up
 System becomes AI-operable.

Phase 9 – Advanced AI Integration & Capstone
Goal
 Deliver a production-grade system and publish the thesis.
Thesis
 The system validates the argument.
Artifacts
full Idiograph system
GitHub repo
demo
design document + public essay
Demo Flow Prompt → agent mutates graph → execution → result
Wrap-Up System and argument are complete.

5. Parallel Track – Thesis & Design Document
Goal
Publish a clear argument supported by the system.
Outputs
design document (in repo)
public essay
Topics
determinism vs probability
semantic structure in pipelines
why AI needs graphs
VFX as precedent

6. Final Deliverables
GitHub repository (Idiograph)
CLI-first system
Query + execution + agent layers
design document
published essay
optional UI
demo

7. Success Criteria
You can:
design semantic graph systems across VFX and AI
build production-grade Python systems
model deterministic pipelines
orchestrate async workflows
expose structured AI tool interfaces
articulate a clear technical thesis publicly

End of Blueprint