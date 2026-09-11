/* Idiograph renderer (D3 v7 + Canvas) — one instrument, two views.
 *
 * The projection has already computed every node's (x, y) in [0,1]; this file
 * is a dumb consumer — it scales those coordinates to the canvas and draws.
 * There is NO force simulation and NO randomness: the same GRAPH renders
 * identically every time. Canvas (not SVG) carries the ~1,885 nodes / ~14,852
 * edges of the full artifact without 16k live DOM elements.
 *
 * FACTORING. Everything above the `VIEWS` table is shared chrome and is the
 * same code for both views: the canvas, the [0,1] → pixel scales, resize,
 * pan/zoom, the hover quadtree and tooltip placement, and the panel builders.
 * A view contributes only four things — `draw`, `hitRadius`, `tooltip` and
 * `panel` — and is selected by `GRAPH.meta.view`. Slice 1 (depth_provenance)
 * draws the artifact the pipeline produced; Slice 2 (declared_graph) draws the
 * Graph that same pipeline declares. Adding the second view moved code; it did
 * not replace the draw layer, and there is no second renderer.
 */
(function () {
  "use strict";

  const meta = GRAPH.meta;
  const nodes = GRAPH.nodes;
  const edges = GRAPH.edges;
  const byId = new Map(nodes.map((n) => [n.node_id, n]));

  const DIR_COLOR = {
    seed: getVar("--seed"),
    backward: getVar("--backward"),
    forward: getVar("--forward"),
    mixed: getVar("--mixed"),
  };
  const CITES = getVar("--cites");
  const CO_CITATION = getVar("--co-citation");
  const NODE_DEFAULT = "#6a6a7a";
  // Ring for the directed shared-foundation nodes (cited by BOTH roots) so the
  // ~dozen marks are findable inside the equidistant column.
  const FOUNDATION_RING = getVar("--ink");
  // Declared-graph palette. One fill and one stroke for EVERY node — the LLM
  // node is drawn exactly as its neighbours are, which is the claim this view
  // makes. There is no per-node colour lookup here on purpose.
  const NODE_FILL = getVar("--node-fill");
  const NODE_EDGE = getVar("--node-edge");
  const WIRE = getVar("--wire");
  const PORT_DOT = getVar("--ink-faint");
  const INK = getVar("--ink");
  const INK_DIM = getVar("--ink-dim");

  const canvas = document.getElementById("graph");
  const ctx = canvas.getContext("2d");
  const stage = document.getElementById("stage");
  const tooltip = document.getElementById("tooltip");

  let width = 0;
  let height = 0;
  let dpr = window.devicePixelRatio || 1;
  let transform = d3.zoomIdentity;

  // Normalized [0,1] → pixel, with a small inset so glyphs never touch the edge.
  let sx = d3.scaleLinear();
  let sy = d3.scaleLinear();

  function resize() {
    const rect = stage.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    const pad = 26;
    sx.domain([0, 1]).range([pad, width - pad]);
    sy.domain([0, 1]).range([pad, height - pad]);
    draw();
  }

  // ── Draw (shared transform; the view owns what goes inside) ────────────────
  function draw() {
    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    ctx.translate(transform.x, transform.y);
    ctx.scale(transform.k, transform.k);
    view.draw(transform.k);

    ctx.globalAlpha = 1;
    ctx.restore();
  }

  // ── View: depth / provenance (Slice 1 — the artifact) ─────────────────────
  const depthProvenance = (function () {
    let showCoCitation = true;

    function drawView(k) {
      // Edges first — inferences (co-citation) beneath declarations (cites).
      drawEdges(k);

      // Non-seed nodes, then seeds on top so the two roots are never occluded.
      const seeds = [];
      ctx.lineWidth = 0;
      for (const n of nodes) {
        if (n.is_seed) { seeds.push(n); continue; }
        // Directed shared foundation (cited by both roots): enlarged + ringed.
        const foundation = n.is_cited_by_both;
        const r = (foundation ? 3.6 : 2.1) / k;
        ctx.beginPath();
        ctx.fillStyle = DIR_COLOR[n.traversal_direction] || NODE_DEFAULT;
        ctx.globalAlpha = 0.9;
        ctx.arc(sx(n.x), sy(n.y), r, 0, 2 * Math.PI);
        ctx.fill();
        if (foundation) {
          ctx.globalAlpha = 1;
          ctx.lineWidth = 1.4 / k;
          ctx.strokeStyle = FOUNDATION_RING;
          ctx.stroke();
        }
      }
      // Seeds: larger, gold, ringed — visually distinct from every other node.
      for (const n of seeds) {
        const px = sx(n.x);
        const py = sy(n.y);
        ctx.globalAlpha = 1;
        ctx.beginPath();
        ctx.fillStyle = DIR_COLOR.seed;
        ctx.arc(px, py, 7 / k, 0, 2 * Math.PI);
        ctx.fill();
        ctx.lineWidth = 2.4 / k;
        ctx.strokeStyle = "#16161c";
        ctx.stroke();
        ctx.beginPath();
        ctx.lineWidth = 1.4 / k;
        ctx.strokeStyle = DIR_COLOR.seed;
        ctx.globalAlpha = 0.45;
        ctx.arc(px, py, 12 / k, 0, 2 * Math.PI);
        ctx.stroke();
      }
    }

    function drawEdges(k) {
      // cites — solid declarations.
      ctx.setLineDash([]);
      ctx.strokeStyle = CITES;
      ctx.globalAlpha = 0.16;
      ctx.lineWidth = 0.6 / k;
      ctx.beginPath();
      for (const e of edges) {
        if (e.type !== "cites") continue;
        const s = byId.get(e.source_id);
        const t = byId.get(e.target_id);
        if (!s || !t) continue;
        ctx.moveTo(sx(s.x), sy(s.y));
        ctx.lineTo(sx(t.x), sy(t.y));
      }
      ctx.stroke();

      if (!showCoCitation) return;
      // co_citation — dashed, dimmer inferences.
      ctx.setLineDash([2.4 / k, 2.4 / k]);
      ctx.strokeStyle = CO_CITATION;
      ctx.globalAlpha = 0.07;
      ctx.lineWidth = 0.5 / k;
      ctx.beginPath();
      for (const e of edges) {
        if (e.type !== "co_citation") continue;
        const s = byId.get(e.source_id);
        const t = byId.get(e.target_id);
        if (!s || !t) continue;
        ctx.moveTo(sx(s.x), sy(s.y));
        ctx.lineTo(sx(t.x), sy(t.y));
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    function tooltipHtml(n) {
      const depths = Object.entries(n.hop_depth_per_root || {})
        .map(([, d]) => d)
        .join(" / ");
      let html =
        '<div class="tt-title">' + esc(n.title || n.node_id) + "</div>" +
        '<div class="tt-meta">' +
        (n.year != null ? n.year + " · " : "") +
        (n.citation_count != null ? n.citation_count + " citations" : "") +
        "</div>" +
        '<div class="tt-meta tt-dir">' +
        (n.is_seed ? "★ seed · " : "") +
        esc(n.traversal_direction || "?") +
        (n.is_cited_by_both ? " · shared foundation (cited by both roots)" : "") +
        "</div>" +
        '<div class="tt-meta">hop depth to seeds: ' + esc(depths) +
        " · pagerank " + (n.pagerank != null ? n.pagerank.toFixed(5) : "—") +
        "</div>";
      if (n.lag_caveat) {
        html += '<div class="tt-lag">⚠ forward signal carries a 12–18 mo ' +
          "citation lag (Node 4)</div>";
      }
      return html;
    }

    function panelHtml() {
      const dir = meta.traversal_direction_counts || {};
      const cs = meta.co_citation_strength || {};
      const seeds = meta.seeds || [];

      return [
        section("Graph", [
          stat("nodes", meta.node_count.toLocaleString()),
          stat("edges", meta.edge_count.toLocaleString()),
          stat("— cites", meta.cites_count.toLocaleString()),
          stat("— co-citation", meta.co_citation_count.toLocaleString()),
          stat("communities", meta.community_count + " (" + meta.community_algorithm + ")"),
          stat("shared foundation", (meta.shared_foundation_count || 0).toLocaleString()),
        ]),

        section("Seeds (the two roots)",
          seeds.map((s) =>
            '<div class="legend-row"><span class="swatch seed"></span>' +
            '<span class="label"><b>' + esc(clip(s.title, 46)) + "</b>" +
            (s.year ? " · " + s.year : "") +
            ' <span style="color:var(--ink-faint)">(pole ' + s.side + ")</span></span></div>"
          ).join("")
        ),

        section("Node — traversal direction", [
          legend("seed", "seed", "the two roots", dir.seed),
          legend("backward", "backward", "foundation the seed cites", dir.backward),
          legend("forward", "forward", "emerging work citing the seed", dir.forward),
          legend("mixed", "mixed", "reachable both ways", dir.mixed),
        ].join("")),

        section("Edge type", [
          '<div class="legend-row"><span class="edge-key cites"></span>' +
            '<span class="label"><b>cites</b> — a declaration (solid)</span></div>',
          '<div class="legend-row"><span class="edge-key co-citation"></span>' +
            '<span class="label"><b>co-citation</b> — an inference (dashed)</span></div>',
          '<label class="toggle"><input type="checkbox" id="cocite-toggle" checked>' +
            "<span>show co-citation edges</span></label>",
        ].join("")),

        section("Layout", [
          '<div class="caveat">Vertical = combined hop depth from both seeds ' +
            "(seeds at top). Horizontal = seed lean: <b>left</b> nearer " +
            esc(clip(seeds[0] && seeds[0].title, 22)) + ", <b>right</b> nearer " +
            esc(clip(seeds[1] && seeds[1].title, 22)) + ", <b>centre</b> equidistant. " +
            "The <b>shared foundation</b> (ringed marks) is the directed subset " +
            "within that column — the " +
            (meta.shared_foundation_count || 0).toLocaleString() +
            " papers <b>both</b> seeds directly cite. Deterministic; not " +
            "force-directed.</div>",
        ].join("")),

        section("Provenance & caveats", [
          '<div class="caveat cycle"><b>' + meta.cycle_suppression_count +
            " edge(s) suppressed</b> by Node 4.5 cycle-cleaning (" +
            meta.cycles_detected_count + " cycle(s) detected, " +
            meta.cycle_iterations + " iteration(s)). " + esc(meta.caveats.cycle_suppression) +
            "</div>",
          '<div class="caveat local"><b>Co-citation strength ' +
            (cs.min != null ? "(" + cs.min + "–" + cs.max + ") " : "") +
            "is a " + esc(cs.label || "local measure") + ".</b> " +
            esc(meta.caveats.co_citation_local) + "</div>",
          '<div class="caveat lag"><b>' + (meta.lag_caveat_count || 0) +
            " node(s) carry the forward citation-lag caveat.</b> " +
            esc(meta.caveats.citation_lag) + "</div>",
        ].join("")),
      ].join("");
    }

    function bind() {
      const toggle = document.getElementById("cocite-toggle");
      toggle.addEventListener("change", (e) => {
        showCoCitation = e.target.checked;
        draw();
      });
    }

    return {
      draw: drawView,
      hitRadius: () => 14 / (sx(1) - sx(0)) || 0.03,
      tooltip: tooltipHtml,
      panel: panelHtml,
      bind: bind,
      hint: "scroll to zoom · drag to pan · hover a node",
    };
  })();

  // ── View: declared graph (Slice 2 — the pipeline itself) ──────────────────
  const declaredGraph = (function () {
    // One size for every node, straight off meta. There is deliberately no
    // per-node size: the LLM node is drawn at the same weight and shape as
    // every other node, and the contract offers no field that could vary it.
    const size = meta.node_size || { w: 0.1, h: 0.05 };

    function boxPx(n) {
      const w = sx(size.w) - sx(0);
      const h = sy(size.h) - sy(0);
      return { x: sx(n.x) - w / 2, y: sy(n.y) - h / 2, w: w, h: h };
    }

    function drawView(k) {
      drawWires(k);
      drawBoxes(k);
    }

    function drawWires(k) {
      // Every edge is drawn between its OWN port anchors, so two edges running
      // between the same node pair on different ports stay two lines. All edges
      // in this graph are DATA — a uniform field gets no visual encoding, so
      // there is one wire treatment and no solid/dashed split to read into.
      ctx.setLineDash([]);
      ctx.strokeStyle = WIRE;
      ctx.lineWidth = 1.1 / k;
      ctx.globalAlpha = 0.75;
      for (const e of edges) {
        const x1 = sx(e.x1), y1 = sy(e.y1), x2 = sx(e.x2), y2 = sy(e.y2);
        // Horizontal control points: the wire leaves the source's right face
        // and arrives at the target's left face, so direction reads without an
        // arrowhead having to carry it alone.
        const bend = Math.max(18, Math.abs(x2 - x1) * 0.45);
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.bezierCurveTo(x1 + bend, y1, x2 - bend, y2, x2, y2);
        ctx.stroke();
        arrowHead(x2, y2, k);
      }
      ctx.globalAlpha = 1;
    }

    function arrowHead(x, y, k) {
      // Always horizontal: every wire lands on a left face pointing right.
      const a = 5 / k;
      ctx.beginPath();
      ctx.fillStyle = WIRE;
      ctx.moveTo(x, y);
      ctx.lineTo(x - a, y - a * 0.55);
      ctx.lineTo(x - a, y + a * 0.55);
      ctx.closePath();
      ctx.fill();
    }

    function drawBoxes(k) {
      for (const n of nodes) {
        const b = boxPx(n);
        const r = Math.min(6 / k, b.h / 3);
        ctx.beginPath();
        roundRect(b.x, b.y, b.w, b.h, r);
        ctx.fillStyle = NODE_FILL;
        ctx.fill();
        ctx.lineWidth = 1.2 / k;
        ctx.strokeStyle = NODE_EDGE;
        ctx.stroke();

        // Port dots, on the anchors the projection computed.
        ctx.fillStyle = PORT_DOT;
        for (const side of ["inputs", "outputs"]) {
          const anchors = (n.port_anchors || {})[side] || {};
          for (const name in anchors) {
            ctx.beginPath();
            ctx.arc(sx(anchors[name].x), sy(anchors[name].y), 2.1 / k, 0, 2 * Math.PI);
            ctx.fill();
          }
        }

        // Labels: the node id, then its declared type. Font scaled by 1/k so
        // text stays legible at every zoom level, then MEASURED and shrunk if
        // the string is wider than the box — `AnnotateRelationships` is 21
        // characters and the box is sized for it, but a longer stage type must
        // still stay inside its own node rather than bleed over the gutter.
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const room = b.w - 10 / k;
        fitText(n.node_id, sx(n.x), sy(n.y) - 7 / k, 13 / k, room, INK, "600 ");
        fitText(n.type, sx(n.x), sy(n.y) + 8 / k, 10.5 / k, room, INK_DIM, "");
      }
      ctx.textAlign = "start";
      ctx.textBaseline = "alphabetic";
    }

    function fitText(label, cx, cy, px, room, colour, weight) {
      const face = "px -apple-system, BlinkMacSystemFont, sans-serif";
      ctx.fillStyle = colour;
      ctx.font = weight + px + face;
      const measured = ctx.measureText(label).width;
      if (measured > room && measured > 0) {
        ctx.font = weight + (px * room / measured) + face;
      }
      ctx.fillText(label, cx, cy);
    }

    function roundRect(x, y, w, h, r) {
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    }

    function portList(names) {
      if (names == null) return "<i>undeclared</i>";
      if (!names.length) return "<i>none (declared empty)</i>";
      return names.map(esc).join(", ");
    }

    // One template for EVERY node. The conditionality rows are present for all
    // nodes and empty for the ones that declare none — the node that does
    // declare them is not thereby marked out as the interesting one.
    function tooltipHtml(n) {
      let html =
        '<div class="tt-title">' + esc(n.node_id) + "</div>" +
        '<div class="tt-meta">' + esc(n.type) + " · rank " + n.rank +
        " of " + (meta.rank_count - 1) + "</div>" +
        '<div class="tt-meta tt-ports"><b>in</b> ' + portList(n.input_ports) + "</div>" +
        '<div class="tt-meta tt-ports"><b>out</b> ' + portList(n.output_ports) + "</div>" +
        '<div class="tt-meta">params: ' +
        (n.param_keys.length ? n.param_keys.map(esc).join(", ") : "<i>none</i>") +
        "</div>" +
        '<div class="tt-meta">resources: ' +
        (n.resources == null
          ? "<i>undeclared</i>"
          : (n.resources.length ? n.resources.map(esc).join(", ") : "<i>none</i>")) +
        "</div>" +
        '<div class="tt-meta">' + n.upstream_count + " upstream · " +
        n.downstream_count + " downstream</div>";
      if (n.enabled_when != null) {
        html += '<div class="tt-meta">gated on param <b>' + esc(n.enabled_when) +
          "</b>; when disabled forwards " +
          Object.keys(n.disabled_passthrough || {}).map(esc).join(", ") +
          "</div>";
      }
      return html;
    }

    function panelHtml() {
      const typeRows = Object.keys(meta.edge_type_counts).map((t) =>
        stat("— " + t, meta.edge_type_counts[t].toLocaleString())
      );
      return [
        section("Declared graph", [
          stat("graph", meta.graph_name + " v" + meta.graph_version),
          stat("nodes", meta.node_count.toLocaleString()),
          stat("edges", meta.edge_count.toLocaleString()),
        ].concat(typeRows).concat([
          stat("execution ranks", meta.rank_count),
          stat("longest chain", meta.longest_chain_length + " nodes"),
          stat("config-gated nodes", meta.conditional_node_count),
        ])),

        section("Execution order",
          meta.ranks.map((r, i) =>
            '<div class="stat-row"><span class="k">rank ' + i +
            '</span><span class="v">' + esc(r.join(", ")) + "</span></div>"
          ).join("")
        ),

        section("Run-supplied resources",
          meta.resource_names.length
            ? meta.resource_names.map((r) =>
                '<div class="stat-row"><span class="k">' + esc(r) +
                '</span><span class="v">' +
                nodes.filter((n) => (n.resources || []).indexOf(r) >= 0).length +
                " node(s)</span></div>"
              ).join("")
            : '<div class="caveat">none declared</div>'
        ),

        section("Layout", [
          '<div class="caveat">Left to right is execution order: a node\'s ' +
            "column is its <b>longest-path depth</b> from the head, so every " +
            "wire advances at least one rank. Wires run port to port — an edge " +
            "is identified by <b>(source, from_port, target, to_port)</b>, not " +
            "by its node pair. Deterministic; not force-directed.</div>",
        ].join("")),

        section("Caveats", [
          '<div class="caveat declaration"><b>Declared, not traced.</b> ' +
            esc(meta.caveats.declaration_vs_execution) + "</div>",
          '<div class="caveat">' + esc(meta.caveats.port_identity) + "</div>",
        ].join("")),
      ].join("");
    }

    return {
      draw: drawView,
      hitRadius: () => Math.max(size.w, size.h) * 0.7,
      tooltip: tooltipHtml,
      panel: panelHtml,
      bind: null,
      hint: "scroll to zoom · drag to pan · hover a node",
    };
  })();

  // ── View selection ─────────────────────────────────────────────────────────
  const VIEWS = {
    depth_provenance: depthProvenance,
    declared_graph: declaredGraph,
  };
  const view = VIEWS[meta.view] || depthProvenance;

  // ── Zoom / pan ─────────────────────────────────────────────────────────────
  const zoom = d3
    .zoom()
    .scaleExtent([0.5, 40])
    .on("zoom", (event) => {
      transform = event.transform;
      draw();
    });
  d3.select(canvas).call(zoom);

  // ── Hover tooltip (quadtree over data-space positions) ─────────────────────
  const quad = d3
    .quadtree()
    .x((n) => n.x)
    .y((n) => n.y)
    .addAll(nodes);

  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    // Screen → world (undo zoom) → data (undo scales).
    const wx = (mx - transform.x) / transform.k;
    const wy = (my - transform.y) / transform.k;
    const dataX = sx.invert(wx);
    const dataY = sy.invert(wy);
    const found = quad.find(dataX, dataY, view.hitRadius());
    if (found) showTooltip(found, event);
    else hideTooltip();
  });
  canvas.addEventListener("mouseleave", hideTooltip);

  function showTooltip(n, event) {
    tooltip.innerHTML = view.tooltip(n);
    tooltip.hidden = false;
    const rect = stage.getBoundingClientRect();
    let left = event.clientX - rect.left + 16;
    let top = event.clientY - rect.top + 16;
    if (left + 330 > width) left = event.clientX - rect.left - 330;
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }
  function hideTooltip() { tooltip.hidden = true; }

  // ── Panel ─────────────────────────────────────────────────────────────────
  buildPanel();

  function buildPanel() {
    document.getElementById("panel-body").innerHTML = view.panel();
    if (view.bind) view.bind();
    const hint = document.getElementById("hint");
    if (hint && view.hint) hint.textContent = view.hint;
  }

  function section(title, rowsOrHtml) {
    const inner = Array.isArray(rowsOrHtml) ? rowsOrHtml.join("") : rowsOrHtml;
    return '<div class="section"><h2>' + esc(title) + "</h2>" + inner + "</div>";
  }
  function stat(k, v) {
    return '<div class="stat-row"><span class="k">' + esc(k) +
      '</span><span class="v">' + esc(v) + "</span></div>";
  }
  function legend(cls, name, desc, count) {
    return '<div class="legend-row"><span class="swatch ' + cls + '"></span>' +
      '<span class="label"><b>' + esc(name) + "</b> — " + esc(desc) +
      (count != null ? " (" + count.toLocaleString() + ")" : "") +
      "</span></div>";
  }

  // ── utils ──────────────────────────────────────────────────────────────────
  function getVar(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function clip(s, n) {
    s = s == null ? "" : String(s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  window.addEventListener("resize", resize);
  resize();
})();
