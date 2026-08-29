"""The embedded Anchor dashboard.

A single self-contained HTML document (no build step, no CDN, no third-party
JS) served from memory by :mod:`anchor.server.api`. It renders the live belief
graph, the contradiction inbox, per-belief explanations and a streaming audit
feed driven by Server-Sent Events.

Every value coming from the API is inserted with ``textContent`` (never
``innerHTML``), so stored belief text cannot become script — memory content is
untrusted input by design.
"""

from __future__ import annotations

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Anchor — belief memory console</title>
<style>
  :root {
    --bg: #070b14;
    --panel: rgba(19, 26, 43, 0.72);
    --panel-solid: #131a2b;
    --line: rgba(120, 145, 200, 0.16);
    --text: #e8edf9;
    --muted: #8ea0c4;
    --accent: #5eead4;
    --accent2: #8b5cf6;
    --warn: #fbbf24;
    --danger: #fb7185;
    --ok: #34d399;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text); min-height: 100vh;
    font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, sans-serif;
    background-image:
      radial-gradient(1100px 520px at 12% -10%, rgba(139,92,246,.22), transparent 60%),
      radial-gradient(900px 460px at 88% 0%, rgba(94,234,212,.14), transparent 60%);
    background-attachment: fixed;
  }
  a { color: var(--accent); }
  .wrap { max-width: 1420px; margin: 0 auto; padding: 22px 26px 60px; }

  header { display: flex; align-items: center; gap: 16px; margin-bottom: 22px; flex-wrap: wrap; }
  .logo { display: flex; align-items: center; gap: 12px; }
  .logo h1 { margin: 0; font-size: 21px; letter-spacing: -.02em; font-weight: 650; }
  .logo span { color: var(--muted); font-size: 12.5px; display: block; font-weight: 400; }
  .spacer { flex: 1; }

  .pill {
    display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px;
    border: 1px solid var(--line); border-radius: 999px; background: var(--panel);
    font-size: 12px; color: var(--muted); backdrop-filter: blur(8px);
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
  .dot.live { animation: pulse 1.8s ease-out infinite; }
  .dot.bad { background: var(--danger); }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(52,211,153,.6); }
    70%  { box-shadow: 0 0 0 9px rgba(52,211,153,0); }
    100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); }
  }

  button {
    font: inherit; font-size: 12.5px; color: var(--text); cursor: pointer;
    background: rgba(139,92,246,.16); border: 1px solid rgba(139,92,246,.4);
    padding: 7px 13px; border-radius: 9px; transition: .16s;
  }
  button:hover { background: rgba(139,92,246,.3); transform: translateY(-1px); }
  button.ghost { background: transparent; border-color: var(--line); color: var(--muted); }
  button.ghost:hover { color: var(--text); border-color: var(--accent); }
  button.tiny { padding: 4px 9px; font-size: 11px; border-radius: 7px; }

  input, select {
    font: inherit; font-size: 13px; color: var(--text); background: rgba(9,13,24,.75);
    border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; outline: none;
  }
  input:focus, select:focus { border-color: var(--accent); }
  input::placeholder { color: #5a6b8f; }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 12px; margin-bottom: 18px; }
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
    padding: 13px 15px; backdrop-filter: blur(10px);
  }
  .card .k { font-size: 11px; text-transform: uppercase; letter-spacing: .09em;
             color: var(--muted); }
  .card .v { font-size: 26px; font-weight: 620; margin-top: 4px; letter-spacing: -.02em; }
  .card .v.small { font-size: 17px; }

  .grid { display: grid; grid-template-columns: 1.55fr 1fr; gap: 16px; align-items: start; }
  @media (max-width: 1080px) { .grid { grid-template-columns: 1fr; } }

  .panel {
    background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
    backdrop-filter: blur(10px); overflow: hidden; margin-bottom: 16px;
  }
  .panel > h2 {
    margin: 0; padding: 13px 16px; font-size: 12px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .1em; color: var(--muted);
    border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 9px;
  }
  .panel > h2 .count {
    background: rgba(139,92,246,.25); color: var(--text); border-radius: 999px;
    padding: 1px 8px; font-size: 11px; letter-spacing: 0;
  }
  .panel .body { padding: 14px 16px; }
  .panel .body.flush { padding: 0; }
  .scroll { max-height: 330px; overflow-y: auto; }
  .scroll::-webkit-scrollbar { width: 8px; }
  .scroll::-webkit-scrollbar-thumb { background: rgba(120,145,200,.25); border-radius: 8px; }

  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
             margin-bottom: 16px; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
       color: var(--muted); font-weight: 500; padding: 9px 16px;
       border-bottom: 1px solid var(--line); position: sticky; top: 0;
       background: var(--panel-solid); }
  td { padding: 9px 16px; border-bottom: 1px solid rgba(120,145,200,.07);
       vertical-align: middle; }
  tbody tr { cursor: pointer; transition: background .12s; }
  tbody tr:hover { background: rgba(94,234,212,.06); }

  .status { font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em;
            padding: 2.5px 8px; border-radius: 999px; border: 1px solid; white-space: nowrap; }
  .status.active      { color: var(--ok);     border-color: rgba(52,211,153,.4);
                        background: rgba(52,211,153,.1); }
  .status.provisional { color: var(--warn);   border-color: rgba(251,191,36,.4);
                        background: rgba(251,191,36,.1); }
  .status.disputed    { color: var(--danger); border-color: rgba(251,113,133,.4);
                        background: rgba(251,113,133,.12); }
  .status.superseded, .status.retracted {
                        color: var(--muted);  border-color: var(--line);
                        background: rgba(120,145,200,.08); }

  .bar { width: 78px; height: 6px; border-radius: 999px; background: rgba(120,145,200,.16);
         overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 8px; }
  .bar > i { display: block; height: 100%; border-radius: 999px;
             background: linear-gradient(90deg, var(--accent2), var(--accent));
             transition: width .5s cubic-bezier(.4,0,.2,1); }
  .mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; }
  .muted { color: var(--muted); }

  .feed-row { display: flex; gap: 10px; align-items: baseline; padding: 7px 16px;
              border-bottom: 1px solid rgba(120,145,200,.07); font-size: 12.5px;
              animation: slide .35s ease; }
  @keyframes slide { from { opacity: 0; transform: translateX(-8px); } to { opacity: 1; } }
  .feed-row .t { color: var(--muted); font-size: 11px;
                 font-family: ui-monospace, Menlo, monospace; min-width: 62px; }
  .ev { font-size: 10.5px; padding: 2px 7px; border-radius: 6px;
        background: rgba(120,145,200,.13); white-space: nowrap; }
  .ev.created { color: var(--accent); background: rgba(94,234,212,.12); }
  .ev.reinforced { color: var(--ok); background: rgba(52,211,153,.12); }
  .ev.contradiction_detected { color: var(--danger); background: rgba(251,113,133,.14); }
  .ev.contradiction_resolved { color: var(--accent2); background: rgba(139,92,246,.18); }
  .ev.promoted { color: var(--warn); background: rgba(251,191,36,.12); }

  .conflict { border: 1px solid rgba(251,113,133,.28); background: rgba(251,113,133,.06);
              border-radius: 12px; padding: 12px; margin-bottom: 10px; }
  .conflict .vs { display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px;
                  align-items: center; margin: 8px 0 11px; }
  .conflict .claim { background: rgba(9,13,24,.6); border: 1px solid var(--line);
                     border-radius: 9px; padding: 8px 10px; font-size: 12.5px; }
  .conflict .x { color: var(--danger); font-weight: 700; }

  #graph { width: 100%; height: 460px; display: block; }
  .edge { stroke: rgba(120,145,200,.35); fill: none; transition: stroke .3s; }
  .edge.contradiction { stroke: var(--danger); stroke-dasharray: 5 4;
                        animation: dash 1s linear infinite; }
  @keyframes dash { to { stroke-dashoffset: -18; } }
  .nodeC { fill: var(--panel-solid); stroke: var(--accent2); stroke-width: 1.6;
           transition: .3s; }
  .nodeC.source { stroke: var(--warn); }
  .nodeT { fill: var(--text); font-size: 11px; text-anchor: middle; pointer-events: none; }
  .edgeT { fill: var(--muted); font-size: 9.5px; text-anchor: middle; pointer-events: none; }

  .drawer {
    position: fixed; top: 0; right: 0; width: min(520px, 94vw); height: 100vh;
    background: #0b1120; border-left: 1px solid var(--line); z-index: 50;
    transform: translateX(102%); transition: transform .3s cubic-bezier(.4,0,.2,1);
    overflow-y: auto; box-shadow: -24px 0 60px rgba(0,0,0,.5);
  }
  .drawer.open { transform: translateX(0); }
  .drawer .head { padding: 18px 20px; border-bottom: 1px solid var(--line);
                  display: flex; gap: 12px; align-items: flex-start; }
  .drawer .head h3 { margin: 0 0 4px; font-size: 16px; }
  .drawer section { padding: 16px 20px; border-bottom: 1px solid var(--line); }
  .drawer h4 { margin: 0 0 10px; font-size: 11px; text-transform: uppercase;
               letter-spacing: .09em; color: var(--muted); font-weight: 600; }
  .kv { display: flex; justify-content: space-between; padding: 4px 0; font-size: 12.5px; }
  .kv span:first-child { color: var(--muted); }
  .reason { padding: 7px 0 7px 14px; border-left: 2px solid var(--accent2);
            margin-bottom: 8px; font-size: 12.5px; color: #cdd7ee; }
  .gauge { height: 26px; border-radius: 8px; background: rgba(120,145,200,.14);
           position: relative; overflow: hidden; margin: 6px 0 10px; }
  .gauge > .fill { position: absolute; inset: 0 auto 0 0;
                   background: linear-gradient(90deg, var(--accent2), var(--accent));
                   transition: width .6s; }
  .gauge > .lb { position: absolute; top: 0; bottom: 0; width: 2px;
                 background: var(--warn); }
  .empty { padding: 26px 16px; text-align: center; color: var(--muted); font-size: 13px; }
  .toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%) translateY(80px);
           background: var(--panel-solid); border: 1px solid var(--accent);
           color: var(--text); padding: 11px 18px; border-radius: 11px; font-size: 13px;
           transition: transform .3s; z-index: 90; }
  .toast.show { transform: translateX(-50%) translateY(0); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">
      <svg width="34" height="34" viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="7" r="4" stroke="#5eead4" stroke-width="2"/>
        <path d="M16 11v16M8 19a8 8 0 0 0 16 0M6 15h20" stroke="#8b5cf6"
              stroke-width="2" stroke-linecap="round"/>
      </svg>
      <div>
        <h1>Anchor</h1>
        <span>belief memory console</span>
      </div>
    </div>
    <div class="spacer"></div>
    <div class="pill">
      <i class="dot live" id="liveDot"></i><span id="liveText">connecting</span>
    </div>
    <div class="pill"><i class="dot" id="chainDot"></i><span id="chainText">audit chain</span></div>
    <div class="pill mono" id="nsPill">default</div>
  </header>

  <div class="cards" id="cards"></div>

  <div class="toolbar">
    <input id="q" placeholder="Ask memory a question…  e.g. where does the user live?"
           style="flex:1; min-width:260px" />
    <button id="askBtn">Recall</button>
    <span style="width:12px"></span>
    <input id="fs" placeholder="subject" style="width:110px" />
    <input id="fp" placeholder="predicate" style="width:120px" />
    <input id="fo" placeholder="object" style="width:110px" />
    <input id="ft" type="number" step="0.05" min="0" max="1" value="0.9"
           title="source trust" style="width:74px" />
    <button id="obsBtn">Observe</button>
    <span class="spacer"></span>
    <button class="ghost" id="seedBtn">Seed demo</button>
    <button class="ghost" id="decayBtn">Apply decay</button>
  </div>

  <div class="grid">
    <div>
      <div class="panel">
        <h2>Belief graph <span class="count" id="graphCount">0</span>
          <span class="spacer" style="flex:1"></span>
          <label class="muted" style="letter-spacing:0; text-transform:none; font-size:11.5px">
            <input type="checkbox" id="showSources" style="vertical-align:-2px" /> show sources
          </label>
        </h2>
        <div class="body flush"><svg id="graph"></svg></div>
      </div>

      <div class="panel">
        <h2>Beliefs <span class="count" id="beliefCount">0</span>
          <span style="flex:1"></span>
          <label class="muted" style="letter-spacing:0; text-transform:none; font-size:11.5px">
            <input type="checkbox" id="showInactive" style="vertical-align:-2px" /> include retired
          </label>
        </h2>
        <div class="body flush scroll">
          <table>
            <thead><tr><th>claim</th><th>confidence</th><th>status</th>
                       <th>sources</th><th></th></tr></thead>
            <tbody id="beliefRows"></tbody>
          </table>
        </div>
      </div>
    </div>

    <div>
      <div class="panel">
        <h2>Contradiction inbox <span class="count" id="conCount">0</span></h2>
        <div class="body scroll" id="conBody"></div>
      </div>

      <div class="panel">
        <h2>Live audit stream</h2>
        <div class="body flush scroll" id="feed"></div>
      </div>

      <div class="panel" id="recallPanel" style="display:none">
        <h2>Recall results</h2>
        <div class="body flush scroll" id="recallBody"></div>
      </div>
    </div>
  </div>
</div>

<div class="drawer" id="drawer"></div>
<div class="toast" id="toast"></div>

<script>
(function () {
  "use strict";
  var NS = new URLSearchParams(location.search).get("namespace") || "default";
  var TOKEN = new URLSearchParams(location.search).get("token") || "";
  document.getElementById("nsPill").textContent = "namespace: " + NS;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }
  function headers() {
    var h = { "Content-Type": "application/json" };
    if (TOKEN) { h["Authorization"] = "Bearer " + TOKEN; }
    return h;
  }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = headers();
    var sep = path.indexOf("?") === -1 ? "?" : "&";
    return fetch(path + sep + "namespace=" + encodeURIComponent(NS), opts)
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) { throw new Error(j.error || r.statusText); }
          return j;
        });
      });
  }
  function post(path, body) {
    body.namespace = NS;
    return api(path, { method: "POST", body: JSON.stringify(body) });
  }
  var toastEl = document.getElementById("toast"), toastTimer;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 2600);
  }
  function pct(x) { return (100 * (x || 0)).toFixed(0) + "%"; }
  function clock(ts) {
    return new Date((ts || 0) * 1000).toLocaleTimeString([], {
      hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  // ------------------------------------------------------------- stat cards
  function renderCards(s) {
    var by = s.by_status || {};
    var total = Object.keys(by).reduce(function (a, k) { return a + by[k]; }, 0);
    var defs = [
      ["beliefs", total, ""],
      ["active", by.active || 0, ""],
      ["provisional", by.provisional || 0, ""],
      ["disputed", by.disputed || 0, ""],
      ["open conflicts", s.open_contradictions || 0, ""],
      ["avg confidence", s.avg_confidence == null ? "—" : pct(s.avg_confidence), ""],
      ["audit chain", s.audit_intact ? "verified" : "BROKEN", "small"]
    ];
    var box = document.getElementById("cards");
    box.textContent = "";
    defs.forEach(function (d) {
      var c = el("div", "card");
      c.appendChild(el("div", "k", d[0]));
      c.appendChild(el("div", "v " + d[2], d[1]));
      box.appendChild(c);
    });
    var dot = document.getElementById("chainDot");
    dot.className = "dot" + (s.audit_intact ? "" : " bad");
    document.getElementById("chainText").textContent =
      s.audit_intact ? "audit chain verified" : "audit chain broken";
  }

  // ----------------------------------------------------------------- graph
  function renderGraph(g) {
    var svg = document.getElementById("graph");
    var W = svg.clientWidth || 880, H = 460;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.textContent = "";
    document.getElementById("graphCount").textContent = g.nodes.length;
    if (!g.nodes.length) {
      var t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t.setAttribute("x", W / 2); t.setAttribute("y", H / 2);
      t.setAttribute("class", "edgeT"); t.textContent = "no beliefs yet — seed the demo";
      svg.appendChild(t); return;
    }
    var sx = W / 900, sy = H / 520, pos = {};
    g.nodes.forEach(function (n) { pos[n.id] = { x: n.x * sx, y: n.y * sy, n: n }; });

    function svgEl(name, attrs, cls) {
      var e = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
      if (cls) { e.setAttribute("class", cls); }
      return e;
    }
    g.edges.forEach(function (e) {
      var a = pos[e.source], b = pos[e.target];
      if (!a || !b) { return; }
      var line = svgEl("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        "stroke-width": (0.8 + 2.6 * (e.confidence || 0)).toFixed(2),
        opacity: e.kind === "contradiction" ? 0.95 : (0.25 + 0.6 * (e.confidence || 0))
      }, "edge " + e.kind);
      svg.appendChild(line);
      var lbl = svgEl("text", { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 4 }, "edgeT");
      lbl.textContent = e.label;
      svg.appendChild(lbl);
    });
    g.nodes.forEach(function (n) {
      var p = pos[n.id];
      var r = 15 + Math.min(13, 3.2 * n.weight);
      var c = svgEl("circle", { cx: p.x, cy: p.y, r: r },
                    "nodeC" + (n.kind === "source" ? " source" : ""));
      svg.appendChild(c);
      var t = svgEl("text", { x: p.x, y: p.y + 4 }, "nodeT");
      t.textContent = n.label.length > 12 ? n.label.slice(0, 11) + "…" : n.label;
      svg.appendChild(t);
      var title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = n.label;
      c.appendChild(title);
    });
  }

  // --------------------------------------------------------------- beliefs
  function renderBeliefs(list) {
    var tb = document.getElementById("beliefRows");
    tb.textContent = "";
    document.getElementById("beliefCount").textContent = list.length;
    if (!list.length) {
      var tr = el("tr"); var td = el("td", "empty", "memory is empty — click “Seed demo”");
      td.colSpan = 5; tr.appendChild(td); tb.appendChild(tr); return;
    }
    list.forEach(function (b) {
      var tr = el("tr");
      tr.appendChild(el("td", null, b.statement));

      var cTd = el("td");
      var bar = el("span", "bar"); var fill = el("i");
      fill.style.width = pct(b.confidence); bar.appendChild(fill);
      cTd.appendChild(bar);
      cTd.appendChild(el("span", "mono muted", pct(b.confidence)));
      tr.appendChild(cTd);

      var sTd = el("td");
      sTd.appendChild(el("span", "status " + b.status, b.status));
      tr.appendChild(sTd);

      tr.appendChild(el("td", "mono muted",
        (b.provenance || []).length + "×" + " / " + b.reinforcement_count + " reinf"));

      var aTd = el("td");
      var btn = el("button", "tiny ghost", "why?");
      btn.addEventListener("click", function (ev) { ev.stopPropagation(); explain(b.id); });
      aTd.appendChild(btn);
      tr.appendChild(aTd);

      tr.addEventListener("click", function () { explain(b.id); });
      tb.appendChild(tr);
    });
  }

  // ---------------------------------------------------------- contradictions
  function renderContradictions(list) {
    var box = document.getElementById("conBody");
    box.textContent = "";
    document.getElementById("conCount").textContent = list.length;
    if (!list.length) {
      box.appendChild(el("div", "empty", "no open contradictions — memory is coherent"));
      return;
    }
    list.forEach(function (c) {
      var w = el("div", "conflict");
      var head = el("div", "muted mono", c.kind + " conflict · score " +
                    (c.score || 0).toFixed(2));
      w.appendChild(head);

      var vs = el("div", "vs");
      var a = el("div", "claim");
      a.appendChild(el("div", null, c.statement_a));
      a.appendChild(el("div", "mono muted", pct(c.confidence_a)));
      vs.appendChild(a);
      vs.appendChild(el("div", "x", "vs"));
      var b = el("div", "claim");
      b.appendChild(el("div", null, c.statement_b));
      b.appendChild(el("div", "mono muted", pct(c.confidence_b)));
      vs.appendChild(b);
      w.appendChild(vs);

      var row = el("div");
      row.style.display = "flex"; row.style.gap = "6px"; row.style.flexWrap = "wrap";
      [["reliable", "Reliable"], ["newest", "Newest"],
       ["highest_confidence", "Strongest"], ["keep_both", "Both true"]]
        .forEach(function (s) {
          var btn = el("button", "tiny", s[1]);
          btn.addEventListener("click", function () {
            post("/api/resolve", { contradiction_id: c.id, strategy: s[0] })
              .then(function () {
                toast("Resolved via " + s[0] + " — loser superseded, not deleted");
                refresh();
              })
              .catch(function (e) { toast("Error: " + e.message); });
          });
          row.appendChild(btn);
        });
      w.appendChild(row);
      box.appendChild(w);
    });
  }

  // ------------------------------------------------------------------ feed
  var feedCount = 0;
  function pushEvent(e) {
    var feed = document.getElementById("feed");
    var row = el("div", "feed-row");
    row.appendChild(el("span", "t", clock(e.ts)));
    row.appendChild(el("span", "ev " + e.event_type, e.event_type));
    var d = el("span", "muted", (e.payload && e.payload.statement)
      ? e.payload.statement : e.belief_id.slice(0, 16));
    d.style.flex = "1"; d.style.overflow = "hidden"; d.style.textOverflow = "ellipsis";
    d.style.whiteSpace = "nowrap";
    row.appendChild(d);
    feed.insertBefore(row, feed.firstChild);
    if (++feedCount > 120) { feed.removeChild(feed.lastChild); }
  }

  // --------------------------------------------------------------- explain
  function explain(id) {
    api("/api/explain/" + encodeURIComponent(id)).then(function (x) {
      var d = document.getElementById("drawer");
      d.textContent = "";

      var head = el("div", "head");
      var hl = el("div"); hl.style.flex = "1";
      hl.appendChild(el("h3", null, x.belief.statement));
      hl.appendChild(el("div", "mono muted", x.belief.id));
      head.appendChild(hl);
      var close = el("button", "ghost tiny", "close");
      close.addEventListener("click", function () { d.classList.remove("open"); });
      head.appendChild(close);
      d.appendChild(head);

      var s1 = el("section");
      s1.appendChild(el("h4", null, "Posterior confidence"));
      var g = el("div", "gauge");
      var f = el("div", "fill"); f.style.width = pct(x.confidence); g.appendChild(f);
      var lb = el("div", "lb"); lb.style.left = pct(x.lower_bound); g.appendChild(lb);
      s1.appendChild(g);
      [["mean", pct(x.confidence)],
       ["conservative lower bound (used for ranking)", pct(x.lower_bound)],
       ["95% credible interval", pct(x.interval[0]) + " – " + pct(x.interval[1])],
       ["supporting evidence (alpha)", x.supporting.toFixed(2)],
       ["contradicting evidence (beta)", x.contradicting.toFixed(2)],
       ["age / staleness", x.age_days.toFixed(1) + "d / " + x.staleness_days.toFixed(1) + "d"]]
        .forEach(function (kv) {
          var r = el("div", "kv");
          r.appendChild(el("span", null, kv[0]));
          r.appendChild(el("span", "mono", kv[1]));
          s1.appendChild(r);
        });
      d.appendChild(s1);

      var s2 = el("section");
      s2.appendChild(el("h4", null, "Why memory holds this"));
      x.reasons.forEach(function (r) { s2.appendChild(el("div", "reason", r)); });
      d.appendChild(s2);

      var s3 = el("section");
      s3.appendChild(el("h4", null, "Evidence by source"));
      x.sources.forEach(function (src) {
        var r = el("div", "kv");
        r.appendChild(el("span", null, src.source_id + (src.capped ? "  (capped)" : "")));
        r.appendChild(el("span", "mono",
          "+" + src.supporting.toFixed(2) + " / -" + src.contradicting.toFixed(2)));
        s3.appendChild(r);
        var bar = el("div", "bar"); bar.style.width = "100%";
        var fi = el("i"); fi.style.width = pct(src.share); bar.appendChild(fi);
        s3.appendChild(bar);
      });
      if (!x.sources.length) { s3.appendChild(el("div", "muted", "no recorded sources")); }
      d.appendChild(s3);

      var s4 = el("section");
      s4.appendChild(el("h4", null, "If never reinforced again"));
      x.decay_projection.forEach(function (p) {
        var r = el("div", "kv");
        r.appendChild(el("span", null, "in " + p.days + " days"));
        r.appendChild(el("span", "mono", pct(p.confidence)));
        s4.appendChild(r);
      });
      d.appendChild(s4);

      var s5 = el("section");
      s5.appendChild(el("h4", null, "Audit timeline (hash-chained)"));
      x.timeline.forEach(function (e) {
        var r = el("div", "kv");
        var left = el("span");
        left.appendChild(el("span", "ev " + e.event_type, e.event_type));
        r.appendChild(left);
        r.appendChild(el("span", "mono muted", clock(e.ts) + " · " + e.actor));
        s5.appendChild(r);
      });
      d.appendChild(s5);

      d.classList.add("open");
    }).catch(function (e) { toast("Error: " + e.message); });
  }

  // --------------------------------------------------------------- actions
  document.getElementById("askBtn").addEventListener("click", function () {
    var q = document.getElementById("q").value.trim();
    if (!q) { return; }
    api("/api/recall?q=" + encodeURIComponent(q) + "&top_k=8").then(function (r) {
      var box = document.getElementById("recallBody");
      document.getElementById("recallPanel").style.display = "block";
      box.textContent = "";
      if (!r.results.length) { box.appendChild(el("div", "empty", "nothing relevant")); }
      r.results.forEach(function (it) {
        var row = el("div", "feed-row");
        row.appendChild(el("span", "mono t", it.score.toFixed(3)));
        var s = el("span", null, it.belief.statement); s.style.flex = "1";
        row.appendChild(s);
        row.appendChild(el("span", "status " + it.belief.status, it.belief.status));
        row.addEventListener("click", function () { explain(it.belief.id); });
        row.style.cursor = "pointer";
        box.appendChild(row);
      });
    }).catch(function (e) { toast("Error: " + e.message); });
  });
  document.getElementById("q").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { document.getElementById("askBtn").click(); }
  });

  document.getElementById("obsBtn").addEventListener("click", function () {
    var s = document.getElementById("fs").value.trim();
    var p = document.getElementById("fp").value.trim();
    var o = document.getElementById("fo").value.trim();
    if (!s || !p || !o) { toast("subject, predicate and object are required"); return; }
    post("/api/observe", {
      subject: s, predicate: p, object: o,
      trust: parseFloat(document.getElementById("ft").value) || 0.8,
      source_id: "dashboard"
    }).then(function (r) {
      var msg = { created: "New belief recorded",
                  reinforced: "Reinforced — confidence rose",
                  contradicted: "Contradiction detected — nothing was overwritten",
                  coexists: "Stored alongside siblings (multi-valued predicate)" };
      toast(msg[r.action] || r.action);
      document.getElementById("fo").value = "";
      refresh();
    }).catch(function (e) { toast("Error: " + e.message); });
  });

  document.getElementById("seedBtn").addEventListener("click", function () {
    post("/api/seed", {}).then(function (r) {
      toast("Seeded " + r.seeded + " observations · " + r.open_contradictions +
            " open contradiction(s)");
      refresh();
    }).catch(function (e) { toast("Error: " + e.message); });
  });
  document.getElementById("decayBtn").addEventListener("click", function () {
    post("/api/decay", {}).then(function (r) {
      toast("Decayed " + r.decayed + " beliefs, retired " + r.retired);
      refresh();
    }).catch(function (e) { toast("Error: " + e.message); });
  });
  document.getElementById("showSources").addEventListener("change", loadGraph);
  document.getElementById("showInactive").addEventListener("change", refresh);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { document.getElementById("drawer").classList.remove("open"); }
  });

  // ---------------------------------------------------------------- polling
  function loadGraph() {
    var src = document.getElementById("showSources").checked ? "1" : "0";
    api("/api/graph?include_sources=" + src).then(renderGraph).catch(function () {});
  }
  function refresh() {
    api("/api/stats").then(renderCards).catch(function () {});
    var inc = document.getElementById("showInactive").checked ? "1" : "0";
    api("/api/beliefs?include_inactive=" + inc)
      .then(function (r) { renderBeliefs(r.beliefs); }).catch(function () {});
    api("/api/contradictions")
      .then(function (r) { renderContradictions(r.contradictions); }).catch(function () {});
    loadGraph();
  }

  api("/api/audit?limit=40").then(function (r) {
    r.events.slice().reverse().forEach(pushEvent);
  }).catch(function () {});

  var es = new EventSource("/api/events?namespace=" + encodeURIComponent(NS));
  es.onopen = function () { document.getElementById("liveText").textContent = "live"; };
  es.onerror = function () {
    document.getElementById("liveText").textContent = "reconnecting";
    document.getElementById("liveDot").className = "dot bad";
  };
  var pending = null;
  es.onmessage = function (m) {
    var data = JSON.parse(m.data);
    if (data.type === "audit") {
      document.getElementById("liveDot").className = "dot live";
      document.getElementById("liveText").textContent = "live";
      pushEvent(data.event);
      clearTimeout(pending);
      pending = setTimeout(refresh, 320);   // debounce bursts of events
    }
  };

  refresh();
  window.addEventListener("resize", loadGraph);
})();
</script>
</body>
</html>
"""
