// End-to-end test in a real browser: a scripted stand-in for Gemini drives the page, and the SQL and
// Python run in Pyodide exactly as they do for visitors. Needs internet access for the Pyodide CDN.
// Run: node tests/e2e.mjs   (with the playwright package installed)
import { createServer } from "node:http";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { serveSite } from "./serve.mjs";

const call = (name, args) => ({ role: "model", parts: [{ functionCall: { name, args }, thoughtSignature: "sig" }] });
const SCRIPT = [
  call("run_sql", { purpose: "Count rows by area group", query: "SELECT area_group, count(*) AS n FROM area_quarterly GROUP BY 1 ORDER BY 1" }),
  call("run_python", { purpose: "Chart national speeds and fit a trend", code: [
    "from scipy import stats",
    "df = sql(\"SELECT quarter, median_download_mbps FROM area_quarterly WHERE area_group='National' AND network='fixed' ORDER BY quarter\")",
    "fit = stats.linregress(range(len(df)), df.median_download_mbps)",
    "print(f'slope={fit.slope:.2f}')",
    "show(df.tail(3), 'Latest quarters')",
    "import matplotlib.pyplot as plt",
    "plt.plot(df.quarter, df.median_download_mbps); plt.title('National home download speed')",
  ].join("\n") }),
  call("run_python", { purpose: "Spin forever", code: "while True:\n    pass" }),
  call("run_sql", { purpose: "Check the runner recovered", query: "SELECT count(*) AS counties FROM counties" }),
  call("run_sql", { purpose: "Try to read a local file", query: "SELECT * FROM read_csv('/etc/hostname')" }),
  { role: "model", parts: [{ text: "Home speeds rose steadily.\n- **Rural** lagged\n- Mobile caught up" }] },
];

const requests = [];
const mock = createServer((req, res) => {
  const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type", "Content-Type": "application/json" };
  if (req.method === "OPTIONS") { res.writeHead(204, headers).end(); return; }
  if (req.method === "GET") { res.writeHead(200, headers).end(JSON.stringify({ ok: true, live: true })); return; }
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    const { contents } = JSON.parse(body);
    requests.push(contents);
    const content = SCRIPT[requests.length - 1] || { role: "model", parts: [{ text: "Out of script." }] };
    res.writeHead(200, headers).end(JSON.stringify({ content, model: "scripted-test", ms: 5, input_tokens: 10, output_tokens: 2 }));
  });
});
await new Promise((r) => mock.listen(8787, "127.0.0.1", r));
const site = await serveSite(8000);

const browser = await chromium.launch();
const page = await browser.newPage();
page.on("console", (m) => console.log(`[page] ${m.text()}`));
page.on("pageerror", (e) => console.log(`[page error] ${e.message}`));
page.on("requestfailed", (r) => console.log(`[request failed] ${r.url()} ${r.failure()?.errorText}`));
page.on("response", (r) => { if (r.status() >= 300) console.log(`[response] ${r.status()} ${r.url()}`); });
page.on("worker", (w) => console.log(`[worker] ${w.url()}`));
await page.addInitScript(() => {
  window.ANALYST_CONFIG = { workerUrl: "http://127.0.0.1:8787", stepTimeoutMs: 15000 };
});
let failed = false;
try {
  await page.goto("http://127.0.0.1:8000/");
  await page.evaluate(() => window.analyst.ready);
  assert.equal(await page.locator(".chip").count(), 6);
  console.log("CDN from page:", await page.evaluate(async () => {
    try { const r = await fetch("https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.js"); return `${r.status} ${r.headers.get("content-type")}`; }
    catch (e) { return `ERR ${e}`; }
  }));
  console.log("CDN from worker:", await page.evaluate(() => new Promise((resolve) => {
    const src = "try{importScripts('https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.js');postMessage('ok '+typeof loadPyodide)}catch(e){postMessage('ERR '+e)}";
    const w = new Worker(URL.createObjectURL(new Blob([src], { type: "text/javascript" })));
    w.onmessage = (ev) => resolve(ev.data);
    w.onerror = (ev) => resolve(`onerror ${ev.message}`);
  })));

  const t0 = Date.now();
  await page.fill("#question", "How have speeds changed?");
  await page.click("#ask-btn");
  await page.waitForSelector(".answer", { timeout: 300_000 });
  console.log(`Live run finished in ${((Date.now() - t0) / 1000).toFixed(1)}s`);

  const steps = page.locator(".step");
  assert.equal(await steps.count(), 5);
  assert.match(await steps.nth(0).locator(".output").innerText(), /Large metro/);
  const py = steps.nth(1).locator(".output");
  assert.match(await py.innerText(), /slope=/);
  assert.match(await py.innerText(), /Latest quarters/);
  assert.equal(await py.locator("img.chart").count(), 1);
  assert.match(await steps.nth(2).locator(".output").innerText(), /Stopped after 15 seconds/);
  assert.match(await steps.nth(3).locator(".output").innerText(), /3,23\d/);
  assert.match(await steps.nth(4).locator(".error").innerText(), /disabled by configuration|Permission|not exist|No files/i);
  assert.match(await page.locator(".answer .body").innerHTML(), /<li><strong>Rural<\/strong> lagged<\/li>/);
  assert.match(await page.locator(".trace").innerText(), /5 steps/);

  // What the model was sent back after each step.
  const lastToolReply = (n) => requests[n].at(-1).parts[0].functionResponse.response.result;
  assert.equal(requests.length, 6);
  assert.match(lastToolReply(1), /Large metro,60/);
  assert.match(lastToolReply(2), /slope=.*1 chart\(s\) shown/s);
  assert.match(lastToolReply(3), /ERROR:\nStopped after 15 seconds/);
  assert.equal(requests[1][1].parts[0].thoughtSignature, "sig", "the model's own turn goes back unchanged");

  // A saved example replays without calling the model.
  const replayPage = await browser.newPage();
  await replayPage.addInitScript(() => { window.ANALYST_CONFIG = { workerUrl: "http://127.0.0.1:8787" }; });
  await replayPage.route("**/saved-runs.json", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({
    "helene": { question: "Helene?", recorded_at: "2026-11-13", answer: "Speeds **dropped**.",
      trace: { steps: 1, llm_calls: 2, input_tokens: 5, output_tokens: 5, llm_ms: 10, run_ms: 10, model: "m" },
      transcript: [{ step: 1, id: 0, lang: "sql", purpose: "Look", code: "SELECT 1",
        result: { ok: true, stdout: "", images: [], error: null, tables: [{ title: null, columns: ["x"], rows: [[1]], row_count: 1, truncated: false }] } }] },
  }) }));
  await replayPage.goto("http://127.0.0.1:8000/");
  await replayPage.evaluate(() => window.analyst.ready);
  const before = requests.length;
  await replayPage.click(".chip[data-id='helene']");
  await replayPage.waitForSelector(".answer");
  assert.match(await replayPage.locator(".trace").innerText(), /Saved run from 2026-11-13/);
  assert.equal(await replayPage.locator(".live-btn").count(), 1);
  assert.equal(requests.length, before);
  console.log("e2e: all checks passed");
} catch (err) {
  failed = true;
  console.error(err);
  await page.screenshot({ path: "e2e-failure.png", fullPage: true }).catch(() => {});
} finally {
  await browser.close();
  mock.close();
  site.close();
}
process.exit(failed ? 1 : 0);
