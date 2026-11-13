// Cloudflare Worker for Broadband Analyst.
//
// The page runs the agent loop and all of the AI's code in the visitor's browser. This Worker
// only holds the Gemini API key: it takes the conversation so far, adds the system prompt and
// the two tool definitions, asks Gemini for the next turn and hands the reply back.
//
// Settings (Cloudflare dashboard > the Worker > Settings > Variables and Secrets):
//   GEMINI_API_KEY   secret, required
//   GEMINI_MODELS    optional, comma-separated models to try in order;
//                    defaults to gemini-flash-latest,gemini-flash-lite-latest
//   ALLOWED_ORIGINS  optional, comma-separated; defaults to the portfolio site and localhost

const DEFAULT_ORIGINS = [
  "https://edwar281.github.io",
  "https://broadband-analyst-preview.pages.dev", // preview of the ai-analyst branch on Cloudflare Pages
  "http://localhost:8000",
  "http://127.0.0.1:8000",
];
const MAX_BODY_BYTES = 400_000;
const MAX_MESSAGES = 40;
const CALLS_PER_IP_PER_MINUTE = 12;
const GEMINI_TIMEOUT_MS = 90_000;

export const SYSTEM_PROMPT = `You are a data analyst answering questions about US internet speeds, using Speedtest by Ookla open data.
You work in steps: write SQL or Python, look at what comes back, then answer. The visitor watches every step,
so each step needs a short, plain purpose line.

## Data (DuckDB, all in memory)

counties: one row per US county.
  fips (5-digit text), state (2-letter), county_name, population_2020, land_area_sqmi, people_per_sqmi,
  rucc_2023 (USDA Rural-Urban Continuum Code 1-9), area_group ('Large metro', 'Smaller metro',
  'Nonmetro town', 'Rural'), median_household_income (2018-22 ACS). Connecticut counties have no
  rucc, area_group or income.

county_quarterly: one row per county, quarter and network, Q1 2019 to Q2 2026.
  fips, quarter (text like '2024Q4'), network ('fixed' = home broadband, 'mobile' = cellular), tiles,
  tests, devices, median_download_mbps, median_upload_mbps, median_latency_ms, mean_download_mbps,
  share_tests_100_20 (0-1 share of tests in places averaging at least 100 Mbps down and 20 up, the FCC
  broadband benchmark), share_tests_below_25_3 (0-1 share below the old 25/3 benchmark).
  Join to counties on fips.

area_quarterly: the same measures for the whole country and each area_group, computed from tiles.
  area_group ('National', 'Large metro', 'Smaller metro', 'Nonmetro town', 'Rural'), network, quarter,
  tiles, tests, median_download_mbps, median_upload_mbps, median_latency_ms, share_tests_100_20.
  Use this for national or rural-versus-urban trends, not an average of counties.

disruptions: 17 county-quarters an anomaly detector flagged, where test volume surged while speeds fell
  or latency rose. fips, quarter, state, county_name, tests, median_download_mbps, median_latency_ms,
  z_tests, z_download, z_latency (robust z-scores against the county's own trend), known_event.

## Facts that matter
- Medians are test-weighted medians of tile averages. Don't average county medians to get a national
  figure; use area_quarterly. To combine counties, weight by tests.
- People test more when something feels wrong, so speeds describe tests taken, not every household.
- Quarters sort correctly as text. Seasonal swings exist, so compare the same quarter across years or
  pool four quarters.

## Tools
- run_sql: one DuckDB query. You see up to 30 rows; the visitor sees the full table.
- run_python: pandas, numpy, matplotlib, scipy and statsmodels are available. sql(query) returns a
  DataFrame, show(df, title) shows a table, matplotlib figures appear as charts. You can't see charts,
  so print the key numbers. The code runs in the visitor's browser and has no internet access to rely on.
  Each run starts fresh: variables don't carry over between steps. Label chart axes with units and give every chart a title.

## How to work
- Use at most 5 steps. Answer simple lookups with one query.
- If code errors, read the error and fix it.
- Then write the answer for a busy reader: lead with the finding and the numbers behind it in one or two
  sentences, add up to three short bullets if they help, and finish with one line on any caveat that
  matters. No headings. Don't mention tool names, and don't invent numbers you didn't compute.
- If the question is not about this data, say briefly what the data can answer instead.`;

export const TOOLS = [
  {
    name: "run_sql",
    description: "Run one DuckDB SQL query against the Ookla tables. Returns the columns and up to 30 rows, " +
      "and the visitor sees the full result table. Use it to look up and aggregate data.",
    parameters: {
      type: "object",
      properties: {
        purpose: { type: "string", description: "One short sentence, shown to the visitor, saying what this step does." },
        query: { type: "string", description: "A single DuckDB SELECT or WITH query." },
      },
      required: ["purpose", "query"],
    },
  },
  {
    name: "run_python",
    description: "Run Python (pandas, numpy, matplotlib, scipy, statsmodels). sql(query) returns a DataFrame, " +
      "show(df, title) displays a table, and any matplotlib figure is shown as a chart. Printed output comes " +
      "back to you. Each run starts fresh. Use it for charts, statistics and anything SQL can't do easily.",
    parameters: {
      type: "object",
      properties: {
        purpose: { type: "string", description: "One short sentence, shown to the visitor, saying what this step does." },
        code: { type: "string", description: "Python code. Print the numbers you need to see." },
      },
      required: ["purpose", "code"],
    },
  },
];

// Best effort only: each Cloudflare location keeps its own count. Gemini's free-tier quota is the hard cap.
const recentCalls = new Map();

function rateLimited(ip, now = Date.now()) {
  const cutoff = now - 60_000;
  const times = (recentCalls.get(ip) || []).filter((t) => t > cutoff);
  if (times.length >= CALLS_PER_IP_PER_MINUTE) {
    recentCalls.set(ip, times);
    return true;
  }
  times.push(now);
  recentCalls.set(ip, times);
  if (recentCalls.size > 5000) recentCalls.clear();
  return false;
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
  });
}

// Accepts only plain conversation turns: text, function calls the model made, and function responses.
export function validContents(contents) {
  if (!Array.isArray(contents) || contents.length < 1 || contents.length > MAX_MESSAGES) return false;
  if (contents[0]?.role !== "user") return false;
  return contents.every((c) =>
    c && (c.role === "user" || c.role === "model") && Array.isArray(c.parts) && c.parts.length > 0 &&
    c.parts.every((p) => p && typeof p === "object" && !("inlineData" in p) && !("fileData" in p)));
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = env.ALLOWED_ORIGINS ? env.ALLOWED_ORIGINS.split(",").map((s) => s.trim()) : DEFAULT_ORIGINS;
    const cors = allowed.includes(origin)
      ? { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400", Vary: "Origin" }
      : { Vary: "Origin" };
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      return json({ ok: true, live: Boolean(env.GEMINI_API_KEY) }, 200, cors);
    }
    if (url.pathname !== "/chat" || request.method !== "POST") return json({ error: "Not found." }, 404, cors);
    if (!allowed.includes(origin)) return json({ error: "This endpoint only serves the Broadband Analyst page." }, 403, cors);
    if (!env.GEMINI_API_KEY) return json({ error: "Live questions are switched off right now." }, 503, cors);

    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    if (rateLimited(ip)) {
      return json({ error: "That's a lot of questions in a minute for this free demo. Give it a moment." }, 429, cors);
    }

    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) return json({ error: "This conversation got too long. Start a new question." }, 413, cors);
    let body;
    try { body = JSON.parse(text); } catch { return json({ error: "Bad request." }, 400, cors); }
    if (!validContents(body?.contents)) return json({ error: "Bad request." }, 400, cors);

    // Models to try in order. Flash-Lite has its own free quota and is busy less often, so it backs up Flash.
    const models = (env.GEMINI_MODELS || env.GEMINI_MODEL || "gemini-flash-latest,gemini-flash-lite-latest")
      .split(",").map((m) => m.trim()).filter(Boolean);
    // Keep a conversation on the model that started it when possible.
    if (typeof body.model === "string" && models.includes(body.model)) {
      models.splice(models.indexOf(body.model), 1);
      models.unshift(body.model);
    }
    const request_body = JSON.stringify({
      systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
      contents: body.contents,
      tools: [{ functionDeclarations: TOOLS }],
      toolConfig: { functionCallingConfig: { mode: "AUTO" } },
      generationConfig: { maxOutputTokens: 8192 },
    });
    const started = Date.now();
    let res = null;
    let model = models[0];
    let lastFailure = { text: "" };
    // A busy model (500/503) gets one quick retry; a busy or rate-limited one (429) hands over to the next model.
    tryModels: for (const m of models) {
      model = m;
      for (let attempt = 0; attempt < 2; attempt++) {
        if (attempt) await new Promise((r) => setTimeout(r, 2000));
        try {
          res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(m)}:generateContent`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
            body: request_body,
            signal: AbortSignal.timeout(GEMINI_TIMEOUT_MS),
          });
        } catch (err) {
          console.log(JSON.stringify({ event: "error", kind: "network", model: m, attempt, detail: String(err).slice(0, 200) }));
          res = null;
          continue;
        }
        if (res.ok || ![429, 500, 502, 503, 504].includes(res.status)) break tryModels;
        lastFailure = { status: res.status, text: await res.text() };
        console.log(JSON.stringify({ event: "error", kind: "gemini-busy", model: m, attempt, status: res.status }));
        if (res.status === 429) break; // retrying the same model right away won't help
      }
    }
    const ms = Date.now() - started;

    const reasonOf = (raw) => { try { return JSON.parse(raw).error?.message || ""; } catch { return ""; } };
    if (!res) return json({ error: "The AI model didn't respond. Please try again shortly." }, 502, cors);
    if (!res.ok) {
      const raw = res.bodyUsed ? lastFailure.text : await res.text();
      const status = res.status;
      const reason = reasonOf(raw);
      console.log(JSON.stringify({ event: "error", kind: "gemini", status, model, detail: raw.slice(0, 500) }));
      const detail = `Gemini ${status}${reason ? `: ${reason.slice(0, 200)}` : ""}`;
      if (status === 429) {
        return json({ error: "The free AI quota is busy right now. Wait a minute and try again, or try one of the saved examples.", detail }, 429, cors);
      }
      if ([500, 502, 503, 504].includes(status)) {
        return json({ error: "The AI model is very busy right now. Please try again in a minute.", detail }, 503, cors);
      }
      // Google's own error message (for example a bad key or model name) helps whoever runs the site; it holds no secrets.
      return json({ error: "Something went wrong talking to the AI model. Please try again shortly.", detail }, 502, cors);
    }

    const data = await res.json();
    const candidate = data.candidates?.[0] || {};
    const usage = data.usageMetadata || {};
    const out = {
      content: candidate.content || { role: "model", parts: [{ text: "" }] },
      finish: candidate.finishReason || "",
      model,
      model_version: data.modelVersion || model,
      ms,
      input_tokens: usage.promptTokenCount || 0,
      output_tokens: (usage.candidatesTokenCount || 0) + (usage.thoughtsTokenCount || 0),
    };
    if (!out.content.parts) out.content.parts = [{ text: "" }];
    console.log(JSON.stringify({ event: "llm", ms, model: out.model, input_tokens: out.input_tokens,
                                 output_tokens: out.output_tokens, turns: body.contents.length }));
    return json(out, 200, cors);
  },
};
