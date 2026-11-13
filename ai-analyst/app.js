// Broadband Analyst front end.
// The agent loop runs here: Gemini (reached through a small Cloudflare Worker that holds the key)
// decides the next step, the step's SQL or Python runs in sandbox.js inside this browser, and the
// results go back to Gemini until it writes the answer.
const CONFIG = window.ANALYST_CONFIG || {};
const MAX_STEPS = 8;
const MAX_CALLS_PER_TURN = 3;
const RESULT_CHARS = 6000;

const PRESETS = [
  { id: "rural-gap", label: "Rural vs. big-city speeds",
    question: "How much faster has home internet gotten in rural areas since 2019, compared with large metros? Chart the trend." },
  { id: "most-improved", label: "Most improved counties",
    question: "Which 10 counties with at least 10,000 people improved the most in median home download speed between 2019 and the last four quarters?" },
  { id: "income", label: "Does income matter?",
    question: "Is county household income related to home broadband speed? Show a scatter plot with a trend line and tell me how strong the relationship is." },
  { id: "below-benchmark", label: "States left behind",
    question: "In the latest quarter, which states have the most people living in counties where fewer than half of home speed tests meet the FCC 100/20 benchmark?" },
  { id: "helene", label: "Hurricane Helene",
    question: "Show what happened to home internet in Buncombe County, NC around Hurricane Helene in late 2024." },
  { id: "mobile-vs-fixed", label: "Mobile catching up?",
    question: "Compare national median download speeds on mobile and home broadband since 2019. Is mobile catching up?" },
];
const BY_ID = Object.fromEntries(PRESETS.map((p) => [p.id, p]));

const $ = (sel) => document.querySelector(sel);
const runEl = $("#run");
const statusEl = $("#status");
const askBtn = $("#ask-btn");
const input = $("#question");
let busy = false;
let live = false;
let savedRuns = {};
const recorded = {};

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
  document.querySelectorAll(".chip").forEach((c) => (c.disabled = on || (!live && !savedRuns[c.dataset.id])));
}

// ---------------------------------------------------------------- code sandbox (Web Worker)

const sandbox = {
  worker: null,
  ready: null,
  loaded: false,
  pending: new Map(),
  nextId: 1,

  start() {
    if (this.ready) return this.ready;
    this.worker = new Worker("sandbox.js", { type: "module" });
    this.worker.onmessage = (ev) => {
      const p = this.pending.get(ev.data.id);
      if (!p) return;
      this.pending.delete(ev.data.id);
      if (ev.data.error) p.reject(new Error(ev.data.error));
      else p.resolve(ev.data);
    };
    this.worker.onerror = (ev) => this.reset(new Error(ev.message || "The code runner stopped."));
    this.ready = this.call({ type: "init" })
      .then(() => { this.loaded = true; })
      .catch((err) => { this.reset(err); throw err; });
    return this.ready;
  },

  call(msg) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, ...msg });
    });
  },

  reset(err) {
    if (this.worker) this.worker.terminate();
    this.pending.forEach((p) => p.reject(err));
    this.pending.clear();
    this.worker = null;
    this.ready = null;
    this.loaded = false;
  },

  async run(lang, code) {
    const fail = (error) => ({ ok: false, stdout: "", tables: [], images: [], error });
    try {
      await this.start();
    } catch (err) {
      return fail(`The code runner couldn't start in this browser: ${err.message}`);
    }
    const limit = CONFIG.stepTimeoutMs || 30000;
    let timer;
    const timeout = new Promise((resolve) => {
      timer = setTimeout(() => {
        this.reset(new Error("timeout"));
        resolve(fail(`Stopped after ${Math.round(limit / 1000)} seconds. Try a smaller query.`));
      }, limit);
    });
    try {
      return await Promise.race([this.call({ type: "run", lang, code }).then((m) => m.result), timeout]);
    } catch (err) {
      return fail(err.message === "timeout" ? `Stopped after ${Math.round(limit / 1000)} seconds.` : err.message);
    } finally {
      clearTimeout(timer);
    }
  },
};

// ---------------------------------------------------------------- the agent loop

// Compact text version of a step's result for the model.
function summarizeResult(r) {
  const parts = [];
  if (!r.ok) parts.push(`ERROR:\n${String(r.error).slice(0, 2000)}`);
  if (r.stdout) parts.push(`Printed output:\n${String(r.stdout).slice(0, 3000)}`);
  (r.tables || []).slice(0, 3).forEach((t) => {
    const cols = t.columns.slice(0, 30).map(String);
    const rows = t.rows.slice(0, 30);
    const lines = [cols.join(","), ...rows.map((row) => row.slice(0, 30).map((v) => (v === null ? "" : String(v))).join(","))];
    const title = t.title ? ` '${t.title}'` : "";
    parts.push(`Table${title}: ${t.row_count} rows${t.row_count > 30 ? " (first 30 shown)" : ""}\n${lines.join("\n")}`);
  });
  if ((r.images || []).length) parts.push(`${r.images.length} chart(s) shown to the visitor.`);
  return (parts.join("\n\n") || "The code ran and produced no output.").slice(0, RESULT_CHARS);
}

async function nextTurn(contents) {
  const res = await fetch(`${CONFIG.workerUrl.replace(/\/$/, "")}/chat`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ contents }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `The AI service returned ${res.status}.`);
  return body;
}

async function* liveRun(question) {
  const started = performance.now();
  const contents = [{ role: "user", parts: [{ text: question }] }];
  const trace = { llm_calls: 0, input_tokens: 0, output_tokens: 0, llm_ms: 0, run_ms: 0, model: "", steps: 0 };
  const transcript = [];
  let steps = 0;
  let text = "";
  sandbox.start().catch(() => {}); // warm up Python while the model plans
  yield { type: "start", question };
  for (;;) {
    if (steps >= MAX_STEPS) contents.push({ role: "user", parts: [{ text: "You're out of steps. Answer now with what you have." }] });
    yield { type: "thinking" };
    const turn = await nextTurn(contents);
    contents.push(turn.content);
    trace.llm_calls += 1;
    trace.input_tokens += turn.input_tokens;
    trace.output_tokens += turn.output_tokens;
    trace.llm_ms += turn.ms;
    trace.model = turn.model;
    const parts = turn.content.parts || [];
    const calls = parts.filter((p) => p.functionCall).map((p) => p.functionCall);
    text = parts.filter((p) => p.text && !p.thought).map((p) => p.text).join("").trim();
    if (!calls.length || steps >= MAX_STEPS) break;

    steps += 1;
    if (text) yield { type: "note", text };
    const responses = [];
    for (const [i, call] of calls.entries()) {
      if (i >= MAX_CALLS_PER_TURN || !["run_sql", "run_python"].includes(call.name)) {
        responses.push({ functionResponse: { name: call.name, response: { result: "Skipped: run at most 3 steps at a time." } } });
        continue;
      }
      const args = call.args || {};
      const lang = call.name === "run_sql" ? "sql" : "python";
      const step = { step: steps, id: i, lang, purpose: String(args.purpose || ""), code: String((lang === "sql" ? args.query : args.code) || "") };
      yield { type: "step", ...step };
      if (!sandbox.loaded) yield { type: "loading" };
      const t0 = performance.now();
      const result = await sandbox.run(lang, step.code);
      const ms = Math.round(performance.now() - t0);
      trace.run_ms += ms;
      yield { type: "result", step: steps, id: i, ms, result };
      transcript.push({ ...step, result });
      responses.push({ functionResponse: { name: call.name, response: { result: summarizeResult(result) } } });
    }
    contents.push({ role: "user", parts: responses });
  }
  trace.steps = steps;
  trace.seconds = Math.round((performance.now() - started) / 100) / 10;
  const answer = text || "I wasn't able to reach an answer. Try rephrasing the question.";
  yield { type: "answer", answer, trace, transcript };
}

async function* replay(saved) {
  yield { type: "start", question: saved.question, saved: saved.recorded_at };
  for (const s of saved.transcript) {
    yield { type: "step", step: s.step, id: s.id, lang: s.lang, purpose: s.purpose, code: s.code };
    yield { type: "result", step: s.step, id: s.id, result: s.result };
  }
  yield { type: "answer", answer: saved.answer, trace: saved.trace, saved: saved.recorded_at };
}

// ---------------------------------------------------------------- rendering

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
  const parts = [
    `${trace.steps} step${trace.steps === 1 ? "" : "s"}`,
    `${trace.llm_calls} model call${trace.llm_calls === 1 ? "" : "s"}`,
    `${trace.input_tokens.toLocaleString()} tokens in, ${trace.output_tokens.toLocaleString()} out`,
    `model ${(trace.llm_ms / 1000).toFixed(1)}s`,
    `code ${(trace.run_ms / 1000).toFixed(1)}s`,
    trace.model,
  ];
  if (saved) parts.unshift(`Saved run from ${saved}`);
  parts.filter(Boolean).forEach((p) => bar.append(el("span", "", p)));
  if (saved && presetId && live) {
    const again = el("button", "btn ghost live-btn", "Run it live");
    again.type = "button";
    again.addEventListener("click", () => ask({ presetId, live: true }));
    bar.append(again);
  }
  return bar;
}

// ---------------------------------------------------------------- asking

async function ask({ question, presetId, live: forceLive = false }) {
  if (busy) return;
  const preset = BY_ID[presetId];
  const saved = preset && !forceLive ? savedRuns[preset.id] : null;
  const q = preset ? preset.question : (question || "").trim();
  if (!q) return;
  if (!saved && !live) {
    setStatus("Live questions are switched off right now. The saved examples still work.", true);
    return;
  }
  setBusy(true);
  setStatus("");
  runEl.replaceChildren();
  const steps = new Map();
  let waiting = null;
  const showWaiting = (text) => {
    if (waiting) waiting.remove();
    waiting = text ? el("p", "pending", text) : null;
    if (waiting) runEl.append(waiting);
  };

  const handle = (e) => {
    if (e.type === "start") {
      runEl.append(el("h2", "question", e.question));
    } else if (e.type === "thinking") {
      showWaiting("The analyst is thinking…");
    } else if (e.type === "note") {
      showWaiting(null);
      runEl.append(el("p", "note", e.text));
    } else if (e.type === "step") {
      showWaiting(null);
      const node = $("#step-tpl").content.firstElementChild.cloneNode(true);
      const badge = node.querySelector(".badge");
      badge.textContent = e.lang === "sql" ? "SQL" : "Python";
      badge.classList.add(e.lang);
      node.querySelector(".purpose").textContent = e.purpose || `Step ${e.step}`;
      node.querySelector("code").textContent = e.code;
      steps.set(`${e.step}:${e.id}`, node);
      runEl.append(node);
    } else if (e.type === "loading") {
      const node = [...steps.values()].pop();
      const p = node && node.querySelector(".output .pending");
      if (p) p.textContent = "Starting Python and DuckDB in your browser. The first step takes a few seconds…";
    } else if (e.type === "result") {
      const node = steps.get(`${e.step}:${e.id}`);
      if (node) renderResult(node.querySelector(".output"), e.result);
    } else if (e.type === "answer") {
      showWaiting(null);
      const box = el("section", "answer");
      box.append(el("h2", "", "Answer"));
      const body = el("div", "body");
      body.innerHTML = renderMarkdown(e.answer);
      box.append(body, renderTrace(e.trace, e.saved, preset && preset.id));
      runEl.append(box);
      if (preset && !e.saved) {
        recorded[preset.id] = { question: q, transcript: e.transcript, answer: e.answer, trace: e.trace,
                                recorded_at: new Date().toISOString().slice(0, 10) };
      }
    }
  };

  try {
    for await (const e of saved ? replay(saved) : liveRun(q)) handle(e);
  } catch (err) {
    showWaiting(null);
    runEl.append(el("div", "problem", err.message));
  } finally {
    showWaiting(null);
    setBusy(false);
  }
}

async function init() {
  try {
    savedRuns = await (await fetch("saved-runs.json", { cache: "no-cache" })).json();
  } catch { savedRuns = {}; }
  if (CONFIG.workerUrl) {
    try {
      const health = await (await fetch(CONFIG.workerUrl)).json();
      live = Boolean(health.live);
    } catch { live = false; }
  }
  const chips = $("#chips");
  PRESETS.forEach((p) => {
    const b = el("button", "chip", p.label);
    b.type = "button";
    b.title = p.question;
    b.dataset.id = p.id;
    b.setAttribute("role", "listitem");
    b.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true");
      ask({ presetId: p.id });
    });
    chips.append(b);
  });
  if (!live) {
    input.disabled = true;
    input.placeholder = "Live questions are off right now. Try an example above.";
    if (!Object.keys(savedRuns).length) setStatus("The analyst is offline right now. Please check back soon.", true);
  }
  setBusy(false);
}

$("#ask-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = input.value.trim();
  if (q) {
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
    ask({ question: q });
  }
});

// Hooks for the automated tests and for saving fresh example runs (see tests/record.mjs).
window.analyst = {
  presets: PRESETS,
  recorded,
  ask,
  sandbox,
  ready: init(),
  isBusy: () => busy,
};
