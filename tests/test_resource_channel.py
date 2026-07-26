# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph
#
# The node-declared resource channel in core/executor.py.
#
# A resource is declared on the node, supplied at execute time, and never
# enters any content address. The channel is the port fence's deliberate twin:
# same declaration shape, same one-way ratchet, same failure discipline —
# differing only in where the value comes from. Ports pull from upstream nodes;
# resources pull from the run.

import asyncio

import pytest

from idiograph.core.executor import (
    HANDLERS,
    UnsuppliedResourceError,
    execute_graph,
    register_handler,
)
from idiograph.core.models import Edge, Graph, Node


@pytest.fixture(autouse=True)
def clear_handlers():
    """Handler registry is process-global — isolate each test."""
    HANDLERS.clear()
    yield
    HANDLERS.clear()


def _one_node(node: Node) -> Graph:
    """A single-node graph, so a test isolates dispatch from dataflow."""
    return Graph(name="resource-channel", version="1.0", nodes=[node], edges=[])


class TestUnsuppliedResource:
    def test_missing_resource_raises_out_of_execute_graph(self):
        """A node declaring a resource the run did not supply is knowable
        without running anything, so it RAISES rather than becoming a FAILED
        result — the same rule as an unregistered node type."""
        ran: list[str] = []

        async def handler(_params, _inputs, *, resources):
            ran.append("handler")
            return {"ok": True}

        register_handler("Needs", handler)
        graph = _one_node(Node(id="n", type="Needs", resources=["http_client"]))

        with pytest.raises(UnsuppliedResourceError) as exc:
            asyncio.run(execute_graph(graph))

        assert "'n'" in str(exc.value)
        assert "http_client" in str(exc.value)
        assert ran == []  # nothing executed

    def test_check_precedes_every_handler_in_the_graph(self):
        """The check runs ONCE over the whole graph before the execution loop:
        a node that would have succeeded earlier in topological order does not
        get to run before a later node's missing resource halts the run."""
        ran: list[str] = []

        async def first(_params, _inputs):
            ran.append("first")
            return {"v": 1}

        async def second(_params, _inputs, *, resources):
            ran.append("second")
            return {"v": 2}

        register_handler("First", first)
        register_handler("Second", second)

        graph = Graph(
            name="halt-before-start", version="1.0",
            nodes=[
                Node(id="a", type="First"),
                Node(id="b", type="Second", resources=["anthropic_client"]),
            ],
            edges=[Edge(source="a", target="b", type="DATA")],
        )

        with pytest.raises(UnsuppliedResourceError):
            asyncio.run(execute_graph(graph))

        assert ran == []  # not even the satisfiable upstream node ran

    def test_missing_name_names_itself_not_the_supplied_ones(self):
        """A partial supply is still a miss, and the error names what is
        absent — not everything the node asked for."""

        async def handler(_params, _inputs, *, resources):
            return {"ok": True}

        register_handler("Needs", handler)
        graph = _one_node(
            Node(id="n", type="Needs", resources=["http_client", "anthropic_client"])
        )

        with pytest.raises(UnsuppliedResourceError) as exc:
            asyncio.run(execute_graph(graph, resources={"http_client": object()}))

        assert "anthropic_client" in str(exc.value)
        assert "'http_client'" not in str(exc.value)


class TestNarrowing:
    def test_declaring_node_receives_only_its_declared_names(self):
        """The declaration on the node is the whole truth about what its
        handler can reach: a run supplying a superset hands over the declared
        subset and nothing else."""
        seen: list[dict] = []

        async def handler(_params, _inputs, *, resources):
            seen.append(resources)
            return {"ok": True}

        register_handler("Needs", handler)
        graph = _one_node(Node(id="n", type="Needs", resources=["http_client"]))

        http, anthropic_c, secret = object(), object(), object()
        results = asyncio.run(execute_graph(graph, resources={
            "http_client": http,
            "anthropic_client": anthropic_c,
            "unrelated_secret": secret,
        }))

        assert results["n"]["status"] == "SUCCESS"
        assert seen[0] == {"http_client": http}

    def test_empty_list_is_a_declaration(self):
        """`resources=[]` declares — the handler is invoked WITH the keyword,
        carrying an empty mapping. Declaring nothing is not the same as not
        declaring."""
        seen: list[dict] = []

        async def handler(_params, _inputs, *, resources):
            seen.append(resources)
            return {"ok": True}

        register_handler("DeclaresNothing", handler)
        graph = _one_node(Node(id="n", type="DeclaresNothing", resources=[]))

        results = asyncio.run(execute_graph(graph, resources={"http_client": object()}))

        assert results["n"]["status"] == "SUCCESS"
        assert seen == [{}]

    def test_declaring_node_is_supplied_even_when_run_supplies_nothing(self):
        """An empty declaration needs no supply, so the single-argument
        `execute_graph(graph)` call stays valid for it."""
        seen: list[dict] = []

        async def handler(_params, _inputs, *, resources):
            seen.append(resources)
            return {"ok": True}

        register_handler("DeclaresNothing", handler)
        graph = _one_node(Node(id="n", type="DeclaresNothing", resources=[]))

        results = asyncio.run(execute_graph(graph))

        assert results["n"]["status"] == "SUCCESS"
        assert seen == [{}]


class TestLegacyCoexistence:
    def test_legacy_handler_called_with_exactly_two_positional_args(self):
        """`resources is None` is the legacy side of the ratchet. Its handler
        signature is untouched — two positional arguments, no keyword — and it
        coexists with a declaring node in one graph."""
        legacy_calls: list[tuple] = []
        declared_calls: list[dict] = []

        async def legacy(*args, **kwargs):
            legacy_calls.append((args, kwargs))
            return {"v": 1}

        async def declaring(_params, _inputs, *, resources):
            declared_calls.append(resources)
            return {"v": 2}

        register_handler("Legacy", legacy)
        register_handler("Declaring", declaring)

        client = object()
        graph = Graph(
            name="mixed", version="1.0",
            nodes=[
                Node(id="a", type="Legacy", params={"k": "v"}),
                Node(id="b", type="Declaring", resources=["http_client"]),
            ],
            edges=[Edge(source="a", target="b", type="DATA")],
        )

        results = asyncio.run(execute_graph(graph, resources={"http_client": client}))

        assert results["a"]["status"] == "SUCCESS"
        assert results["b"]["status"] == "SUCCESS"

        (args, kwargs) = legacy_calls[0]
        assert len(args) == 2
        assert kwargs == {}
        assert args[0] == {"k": "v"}

        # The declaring node in the same run still got its narrowed mapping.
        assert declared_calls[0] == {"http_client": client}

    def test_legacy_node_unaffected_by_a_supplied_run(self):
        """Supplying resources to the run does not leak into a legacy node —
        the ratchet turns per node, on that node's own declaration."""
        legacy_calls: list[tuple] = []

        async def legacy(*args, **kwargs):
            legacy_calls.append((args, kwargs))
            return {"v": 1}

        register_handler("Legacy", legacy)
        graph = _one_node(Node(id="a", type="Legacy"))

        results = asyncio.run(execute_graph(graph, resources={"http_client": object()}))

        assert results["a"]["status"] == "SUCCESS"
        assert len(legacy_calls[0][0]) == 2
        assert legacy_calls[0][1] == {}


# ── Converted arXiv handlers ─────────────────────────────────────────────────
#
# Convention B is retired: these handlers construct no client and read no
# process environment. Driven against fakes, so no network and no credentials.


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True


class _FakeHTTPClient:
    """Stands in for the httpx.AsyncClient the composition root would build."""

    def __init__(self, text: str):
        self._response = _FakeResponse(text)
        self.calls: list[tuple] = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return self._response


_ATOM = "http://www.w3.org/2005/Atom"

_FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="{_ATOM}">
  <entry>
    <title>Attention Is All You Need</title>
    <summary>We propose the Transformer.</summary>
    <author><name>Vaswani</name></author>
    <author><name>Shazeer</name></author>
  </entry>
</feed>"""


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [type("Block", (), {"text": text})()]


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text
        self.kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.kwargs.append(kwargs)
        return _FakeMessage(self._text)


class _FakeAnthropicClient:
    """Stands in for anthropic.AsyncAnthropic. Never constructed by a handler."""

    def __init__(self, text: str = "a response"):
        self.messages = _FakeMessages(text)


class TestConvertedFetchAbstract:
    def test_output_built_from_the_injected_client_response(self):
        """The handler parses the fake's payload — proof the client came in
        through the resource channel rather than being built inside."""
        from idiograph.domains.arxiv.handlers import fetch_abstract

        client = _FakeHTTPClient(_FEED)
        out = asyncio.run(fetch_abstract(
            {"paper_id": "1706.03762"}, {}, resources={"http_client": client},
        ))

        assert out["paper_id"] == "1706.03762"
        assert out["title"] == "Attention Is All You Need"
        assert out["abstract"] == "We propose the Transformer."
        assert out["authors"] == "Vaswani, Shazeer"
        assert client.calls == [
            ("https://export.arxiv.org/api/query", {"id_list": "1706.03762"})
        ]

    def test_resources_is_keyword_only_and_has_no_default(self):
        """A declaring node's handler cannot be silently called without its
        resources — there is no default to fall back to."""
        from idiograph.domains.arxiv.handlers import fetch_abstract

        with pytest.raises(TypeError):
            asyncio.run(fetch_abstract({"paper_id": "x"}, {}))

    def test_missing_entry_still_raises(self):
        """Existing failure behaviour is unchanged by the conversion."""
        from idiograph.domains.arxiv.handlers import fetch_abstract

        client = _FakeHTTPClient(f'<feed xmlns="{_ATOM}"></feed>')
        with pytest.raises(ValueError, match="not found"):
            asyncio.run(fetch_abstract(
                {"paper_id": "nope"}, {}, resources={"http_client": client},
            ))


class TestConvertedLLMCall:
    def _params(self, **over) -> dict:
        base = {
            "prompt_template": "Summarize {title}",
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
        }
        base.update(over)
        return base

    def test_output_built_from_the_injected_client_response(self):
        from idiograph.domains.arxiv.handlers import llm_call

        client = _FakeAnthropicClient("three claims")
        out = asyncio.run(llm_call(
            self._params(),
            {"up": {"title": "T", "abstract": "A"}},
            resources={"anthropic_client": client},
        ))

        assert out["response"] == "three claims"
        assert out["title"] == "T"  # upstream payload still forwarded
        sent = client.messages.kwargs[0]
        assert sent["messages"] == [{"role": "user", "content": "Summarize T"}]

    def test_model_and_max_tokens_come_from_params(self):
        """They moved out of the handler body, so the graph — not the code —
        decides them."""
        from idiograph.domains.arxiv.handlers import llm_call

        client = _FakeAnthropicClient()
        asyncio.run(llm_call(
            self._params(model="some-other-model", max_tokens=64),
            {"up": {"title": "T"}},
            resources={"anthropic_client": client},
        ))

        sent = client.messages.kwargs[0]
        assert sent["model"] == "some-other-model"
        assert sent["max_tokens"] == 64

    def test_missing_model_raises_keyerror(self):
        """No `.get()` default: a silent fallback would let the graph claim one
        model while the run quietly used another."""
        from idiograph.domains.arxiv.handlers import llm_call

        params = self._params()
        del params["model"]

        with pytest.raises(KeyError, match="model"):
            asyncio.run(llm_call(
                params, {"up": {"title": "T"}},
                resources={"anthropic_client": _FakeAnthropicClient()},
            ))

    def test_missing_max_tokens_raises_keyerror(self):
        from idiograph.domains.arxiv.handlers import llm_call

        params = self._params()
        del params["max_tokens"]

        with pytest.raises(KeyError, match="max_tokens"):
            asyncio.run(llm_call(
                params, {"up": {"title": "T"}},
                resources={"anthropic_client": _FakeAnthropicClient()},
            ))

    def test_no_environment_read(self, monkeypatch):
        """Convention B is retired: the handler succeeds with no API key in the
        environment, because it neither reads one nor builds a client."""
        from idiograph.domains.arxiv.handlers import llm_call

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = asyncio.run(llm_call(
            self._params(), {"up": {"title": "T"}},
            resources={"anthropic_client": _FakeAnthropicClient("ok")},
        ))
        assert out["response"] == "ok"


class TestConvertedLLMSummarize:
    def test_forwards_its_declared_resource(self):
        """The forwarder declares and forwards — a forwarder that did not would
        reach a callee that requires one."""
        from idiograph.domains.arxiv.handlers import llm_summarize

        client = _FakeAnthropicClient("a summary")
        out = asyncio.run(llm_summarize(
            {
                "prompt_template": "Summarize {title}",
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
            },
            {"up": {"title": "T"}},
            resources={"anthropic_client": client},
        ))

        assert out["response"] == "a summary"
        assert client.messages.kwargs[0]["model"] == "claude-haiku-4-5-20251001"


class TestArxivPipelineDeclarations:
    def test_declared_nodes_match_the_converted_handlers(self):
        """The three converted handlers' nodes declare; the pure stages do
        not."""
        from idiograph.domains.arxiv.pipeline import ARXIV_PIPELINE

        declared = {n.id: n.resources for n in ARXIV_PIPELINE.nodes}
        assert declared["fetch"] == ["http_client"]
        assert declared["claims"] == ["anthropic_client"]
        assert declared["summarize"] == ["anthropic_client"]
        assert declared["evaluate"] is None

    def test_model_and_max_tokens_are_present_in_params(self):
        """Required params, carried by the graph — llm_call subscripts them."""
        from idiograph.domains.arxiv.pipeline import ARXIV_PIPELINE

        for node_id in ("claims", "summarize"):
            params = ARXIV_PIPELINE.get_node(node_id).params
            assert params["model"] == "claude-haiku-4-5-20251001"
            assert params["max_tokens"] == 512
