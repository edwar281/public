// Ookla analyst front end: sends a question, streams the agent's steps, renders results.
const $ = (sel) => document.querySelector(sel);
const runEl = $("#run");
const statusEl = $("#status");
const askBtn = $("#ask-btn");
const input = $("#question");
let busy = false;
let live = false;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setStatus(text, warn = false) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("warn", warn);
}

function setBusy(on) {
  busy = on;
  askBtn.disabled = on || !live;
  document.querySelectorAll(".chip").forEach((c) => (c.disabled = on));
}

// Small, safe markdown: paragraphs, bullets, **bold** and `code`. Everything is escaped first.
function renderMarkdown(text) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)/);
    if (bullet) {
      if (!list) { list = []; out.push(list); }
      list.push(`<li>${inline(bullet[1])}</li>`);
    } else {
      list = null;
      if (line) out.push(`<p>${inline(line)}</p>`);
    }
  }
  return out.map((b) => (Array.isArray(b) ? `<ul>${b.join("")}</ul>` : b)).join("");
}

function fmt(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") {
    return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
                               : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return String(v);
}

function renderTable(t) {
  const frag = document.createDocumentFragment();
  if (t.title) frag.append(el("p", "table-meta", t.title));
  const wrap = el("div", "table-wrap");
  const table = el("table");
  const thead = el("thead");
  const hr = el("tr");
  t.columns.forEach((c) => hr.append(el("th", "", c)));
  thead.append(hr);
  const tbody = el("tbody");
  t.rows.forEach((row) => {
    const tr = el("tr");
    row.forEach((v) => tr.append(el("td", typeof v === "number" ? "num" : "", fmt(v))));
    tbody.append(tr);
  });
  table.append(thead, tbody);
  wrap.append(table);
  frag.append(wrap);
  const rows = `${t.row_count.toLocaleString()} row${t.row_count === 1 ? "" : "s"}`;
  frag.append(el("p", "table-meta", t.truncated ? `${rows}, first ${t.rows.length} shown` : rows));
  return frag;
}

function renderResult(out, r) {
  out.replaceChildren();
  if (r.stdout) out.append(el("pre", "stdout", r.stdout));
  (r.images || []).forEach((b64) => {
    const img = el("img", "chart");
    img.src = `data:image/png;base64,${b64}`;
    img.alt = "Chart produced by this step";
    out.append(img);
  });
  (r.tables || []).forEach((t) => out.append(renderTable(t)));
  if (!r.ok) {
    out.append(el("div", "error", r.error || "This step failed."));
    out.append(el("p", "error-note", "The analyst sees this error and can correct its code in the next step."));
  }
  if (r.ok && !r.stdout && !(r.images || []).length && !(r.tables || []).length) {
    out.append(el("p", "pending", "Ran with no output."));
  }
}

function renderTrace(trace, saved, presetId) {
  const bar = el("div", "trace");
  const tokens = `${trace.input_tokens.toLocaleString()} tokens in, ${trace.output_tokens.toLocaleString()} out`;
  const parts = [
    `${trace.steps} step${trace.steps === 1 ? "" : "s"}`,
    `${trace.llm_calls} model call${trace.llm_calls === 1 ? "" : "s"}`,
    tokens,
    `model ${(trace.llm_ms / 1000).toFixed(1)}s`,
    `code ${(trace.run_ms / 1000).toFixed(1)}s`,
    trace.model,
  ];
  if (saved) parts.unshift(`Saved run from ${saved}`);
  parts.forEach((p) => bar.append(el("span", "", p)));
  if (saved && presetId && live) {
    const again = el("button", "btn ghost live-btn", "Run it live");
    again.type = "button";
    again.addEventListener("click", () => ask({ preset_id: presetId, live: true }));
    bar.append(again);
  }
  return bar;
}

async function ask(payload) {
  if (busy) return;
  setBusy(true);
  setStatus("");
  runEl.replaceChildren();
  const steps = new Map();
  let thinking = null;
  const showThinking = (on) => {
    if (on && !thinking) { thinking = el("p", "pending", "The analyst is thinking…"); runEl.append(thinking); }
    if (!on && thinking) { thinking.remove(); thinking = null; }
  };
  try {
    const res = await fetch("api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `The server returned ${res.status}.`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        handle(JSON.parse(line));
      }
    }
  } catch (err) {
    showThinking(false);
    runEl.append(el("div", "problem", err.message));
  } finally {
    showThinking(false);
    setBusy(false);
    loadStats();
  }

  function handle(e) {
    if (e.type === "start") {
      runEl.append(el("h2", "question", e.question));
    } else if (e.type === "thinking") {
      showThinking(true);
    } else if (e.type === "note") {
      showThinking(false);
      runEl.append(el("p", "note", e.text));
    } else if (e.type === "step") {
      showThinking(false);
      const node = $("#step-tpl").content.firstElementChild.cloneNode(true);
      const badge = node.querySelector(".badge");
      badge.textContent = e.lang === "sql" ? "SQL" : "Python";
      badge.classList.add(e.lang);
      node.querySelector(".purpose").textContent = e.purpose || `Step ${e.step}`;
      node.querySelector("code").textContent = e.code;
      steps.set(`${e.step}:${e.id}`, node);
      runEl.append(node);
    } else if (e.type === "result") {
      const node = steps.get(`${e.step}:${e.id}`);
      if (node) renderResult(node.querySelector(".output"), e.result);
    } else if (e.type === "answer") {
      showThinking(false);
      const box = el("section", "answer");
      box.append(el("h2", "", "Answer"));
      const body = el("div", "body");
      body.innerHTML = renderMarkdown(e.answer);
      box.append(body, renderTrace(e.trace, e.saved, payload.preset_id));
      runEl.append(box);
    } else if (e.type === "error") {
      showThinking(false);
      runEl.append(el("div", "problem", e.message));
    }
  }
}

async function loadStats() {
  try {
    const s = await (await fetch("api/stats")).json();
    if (s.answers) {
      $("#stats").textContent = `Today: ${s.answers} live answer${s.answers === 1 ? "" : "s"}` +
        (s.median_answer_seconds ? `, median ${s.median_answer_seconds}s each.` : ".");
    }
  } catch { /* stats are optional */ }
}

async function init() {
  try {
    const cfg = await (await fetch("api/config")).json();
    live = cfg.live;
    const chips = $("#chips");
    cfg.presets.forEach((p) => {
      const b = el("button", "chip", p.label);
      b.type = "button";
      b.title = p.question;
      b.setAttribute("role", "listitem");
      b.addEventListener("click", () => {
        document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        ask({ preset_id: p.id });
      });
      chips.append(b);
    });
    if (!live) {
      input.disabled = true;
      input.placeholder = "Live questions are off right now. Try an example above.";
    }
    setBusy(false);
    loadStats();
  } catch {
    setStatus("The analyst is offline right now. Please try again later.", true);
  }
}

$("#ask-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = input.value.trim();
  if (q) {
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
    ask({ question: q });
  }
});

init();
