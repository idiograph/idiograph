from idiograph.core.models import Graph, Node, Edge

ARXIV_PIPELINE: Graph = Graph(
    name="arxiv_abstract_pipeline",
    version="1.0",
    nodes=[
        Node(
            id="fetch",
            type="FetchAbstract",
            params={"paper_id": ""},  # patched at runtime via CLI
        ),
        Node(
            id="claims",
            type="LLMCall",
            params={
                "system": "You are a precise scientific analyst.",
                "prompt_template": (
                    "List the key concrete claims from this abstract as bullet points.\n\n"
                    "Title: {title}\n\nAbstract: {abstract}"
                ),
            },
        ),
        Node(
            id="evaluate",
            type="Evaluator",
            params={
                "keywords": ["method", "model", "result", "performance", "dataset"],
                "threshold": 0.4,
            },
        ),
        Node(
            id="summarize",
            type="LLMSummarize",
            params={
                "system": "You are a technical research communicator.",
                "prompt_template": (
                    "Write a 2-sentence technical summary of this paper for an AI engineer.\n\n"
                    "Title: {title}\n\nAbstract: {abstract}"
                ),
            },
        ),
    ],
    edges=[
        Edge(source="fetch",    target="claims",   type="DATA"),
        Edge(source="claims",   target="evaluate", type="DATA"),
        Edge(source="evaluate", target="summarize", type="CONTROL"),
    ],
)
