/* Idiograph depth/provenance renderer (D3 v7 + Canvas).
 *
 * The projection has already computed every node's (x, y) in [0,1]; this file
 * is a dumb consumer — it scales those coordinates to the canvas and draws.
 * There is NO force simulation and NO randomness: the same GRAPH renders
 * identically every time. Canvas (not SVG) carries the ~1,885 nodes / ~14,852
 * edges of the full artifact without 16k live DOM elements.
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

  const canvas = document.getElementById("graph");
  const ctx = canvas.getContext("2d");
  const stage = document.getElementById("stage");
  const tooltip = document.getElementById("tooltip");

  let width = 0;
  let height = 0;
  let dpr = window.devicePixelRatio || 1;
  let transform = d3.zoomIdentity;
  let showCoCitation = true;

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

  // ── Draw ─────────────────────────────────────────────────────────────────
  function draw() {
    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    ctx.translate(transform.x, transform.y);
    ctx.scale(transform.k, transform.k);
    const k = transform.k;

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
    ctx.globalAlpha = 1;
    ctx.restore();
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
    const found = quad.find(dataX, dataY, 14 / (sx(1) - sx(0)) || 0.03);
    if (found) showTooltip(found, event);
    else hideTooltip();
  });
  canvas.addEventListener("mouseleave", hideTooltip);

  function showTooltip(n, event) {
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
    tooltip.innerHTML = html;
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
    const body = document.getElementById("panel-body");
    const dir = meta.traversal_direction_counts || {};
    const cs = meta.co_citation_strength || {};
    const seeds = meta.seeds || [];

    body.innerHTML = [
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

    const toggle = document.getElementById("cocite-toggle");
    toggle.addEventListener("change", (e) => {
      showCoCitation = e.target.checked;
      draw();
    });
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
