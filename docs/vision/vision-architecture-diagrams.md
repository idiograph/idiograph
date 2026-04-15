# Idiograph — Architecture Diagrams

---

## 1. Module Dependency Graph

Who imports what. Arrows point in the direction of dependency.

```mermaid
graph TD
    subgraph CLI
        main["main.py"]
    end

    subgraph core
        init["core/__init__.py"]
        models["core/models.py"]
        graph["core/graph.py"]
        query["core/query.py"]
        pipeline["core/pipeline.py"]
        config["core/config.py"]
        logging_config["core/logging_config.py"]
    end

    subgraph tests
        conftest["tests/conftest.py"]
        test_models["tests/test_models.py"]
        test_graph["tests/test_graph.py"]
        test_query["tests/test_query.py"]
    end

    subgraph external
        pydantic["pydantic"]
        networkx["networkx"]
        typer["typer"]
        tomllib["tomllib (stdlib)"]
        logging["logging (stdlib)"]
    end

    main --> init
    main --> query
    main --> pydantic

    init --> pipeline
    init --> graph
    init --> config
    init --> logging_config

    pipeline --> models
    graph --> models
    graph --> logging_config
    query --> models
    query --> logging_config
    query --> networkx

    config --> tomllib
    logging_config --> logging

    models --> pydantic
    main --> typer

    conftest --> models
    test_models --> models
    test_models --> pydantic
    test_graph --> graph
    test_query --> query
    test_query --> models
```

---

## 2. Architecture Layer Diagram

The system as zones of responsibility. Arrows show the direction of allowed dependency.

```mermaid
graph TD
    subgraph INTERFACE ["Interface Layer"]
        cli["CLI — main.py
        Entry point. Parses commands.
        Formats and echoes output.
        Owns nothing."]
    end

    subgraph CORE ["Core Layer"]
        direction LR
        models["models.py
        Node · Edge · Graph
        Schema + validation
        Pydantic models"]

        graph_ops["graph.py
        get_node
        get_edges_from
        summarize
        load_graph"]

        query_ops["query.py
        get_downstream · get_upstream
        topological_sort · find_cycles
        validate_integrity
        summarize_intent"]

        pipeline["pipeline.py
        SAMPLE_PIPELINE
        The canonical example graph"]

        cfg["config.py
        load_config
        Reads idiograph.toml
        Falls back to defaults"]

        log["logging_config.py
        setup_logging
        get_logger
        idiograph.* namespace"]
    end

    subgraph TEST ["Test Layer"]
        direction LR
        fixtures["conftest.py
        sample_graph
        cyclic_graph
        Own data — not SAMPLE_PIPELINE"]

        tm["test_models.py
        Schema correctness
        Default behavior
        Mutable default guard"]

        tg["test_graph.py
        Graph function behavior
        Round-trip serialization"]

        tq["test_query.py
        Traversal correctness
        Integrity checking
        Intent summarization"]
    end

    subgraph EXTERNAL ["External Dependencies"]
        direction LR
        pyd["pydantic
        Validation + serialization"]
        nx["networkx
        Graph math
        Traversal · cycles · topo sort"]
        typ["typer
        CLI framework"]
        tom["tomllib
        TOML parsing (stdlib)"]
    end

    subgraph PENDING ["Pending — Phase 6+"]
        direction LR
        executor["executor.py
        Async execution engine
        Topological ordering
        Status updates"]
        handlers["handlers/
        arxiv.py (Phase 6 demo)
        Handler registry
        Domain implementations"]
    end

    INTERFACE --> CORE
    TEST --> CORE
    CORE --> EXTERNAL
    INTERFACE -.->|"will use"| PENDING
    PENDING --> CORE
    PENDING --> EXTERNAL
```

---

## 3. Data Flow — A Query Through the System

How a single CLI command moves through the layers.

```mermaid
sequenceDiagram
    participant User
    participant CLI as main.py
    participant Core as core/__init__.py
    participant Query as query.py
    participant Models as models.py
    participant NX as networkx

    User->>CLI: idiograph query intent
    CLI->>Core: import SAMPLE_PIPELINE
    Core->>Models: Graph / Node / Edge instances
    Models-->>Core: typed, validated graph
    Core-->>CLI: SAMPLE_PIPELINE ready

    CLI->>Query: summarize_intent(SAMPLE_PIPELINE)
    Query->>NX: _build_nx_graph(graph)
    NX-->>Query: DiGraph (transient)
    Query->>Query: derive domain, critical path,
    Query->>Query: control gates, failed nodes
    Query-->>CLI: structured dict (JSON-serializable)
    CLI-->>User: JSON output
```

---

## Key Rules the Diagrams Encode

| Rule | Where Visible |
|---|---|
| CLI never owns data or logic | Diagram 1 — main.py has no arrows going into it from core internals |
| Core never depends on CLI | Diagram 2 — dependency arrows only flow downward |
| networkx is transient | Diagram 3 — NX is used and gone within query.py, never surfaces to CLI |
| Tests own their data | Diagram 2 — conftest.py is isolated, no arrow to SAMPLE_PIPELINE |
| Handlers never touch executor internals | Diagram 2 — pending layer shows handlers and executor as siblings under core |
| External deps stay at the bottom | Diagram 2 — nothing in external imports from core |
