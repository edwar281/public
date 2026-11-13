// Runs the example questions live against the real Worker and saves them to saved-runs.json,
// so visitors' clicks replay instantly without using the Gemini quota.
// Run: node tests/record.mjs [preset-id ...]   (needs config.js to point at the deployed Worker)
import { readFile, writeFile } from "node:fs/promises";
import { chromium } from "playwright";
import { serveSite } from "./serve.mjs";

const OUT = new URL("../saved-runs.json", import.meta.url);
const site = await serveSite(8000);
const browser = await chromium.launch();
const page = await browser.newPage();
page.on("pageerror", (e) => console.log(`[page error] ${e.message}`));
page.on("console", (m) => { if (m.type() === "warning" || m.type() === "error") console.log(`[page] ${m.text()}`); });
let saved = 0;
try {
  await page.goto("http://127.0.0.1:8000/");
  await page.evaluate(() => window.analyst.ready);
  const ids = process.argv.slice(2).length ? process.argv.slice(2) : await page.evaluate(() => window.analyst.presets.map((p) => p.id));
  const runs = JSON.parse(await readFile(OUT, "utf8"));
  for (const id of ids) {
    console.log(`Running ${id}...`);
    await page.evaluate((presetId) => window.analyst.ask({ presetId, live: true }), id);
    const run = await page.evaluate((presetId) => window.analyst.recorded[presetId], id);
    const problem = await page.locator(".problem").allInnerTexts();
    if (!run || problem.length) {
      console.log(`  failed: ${problem.join(" ") || "no answer"}`);
      continue;
    }
    const failedSteps = run.transcript.filter((s) => !s.result.ok).length;
    console.log(`  ${run.trace.steps} steps (${failedSteps} with errors), ${run.trace.seconds}s: ${run.answer.slice(0, 120)}`);
    runs[id] = run;
    saved += 1;
    await page.waitForTimeout(15_000); // stay well inside the free tier's per-minute limit
  }
  await writeFile(OUT, `${JSON.stringify(runs, null, 1)}\n`);
  console.log(`Saved ${saved} of ${ids.length} runs to saved-runs.json`);
} finally {
  await browser.close();
  site.close();
}
process.exit(saved ? 0 : 1);
