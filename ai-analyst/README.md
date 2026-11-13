# Broadband Analyst

An AI analyst for seven years of US internet speed tests. Ask a question in plain English and it writes SQL and Python, runs them, reads the results and explains what it found. Every step, the code behind it, and the tokens and time it took are shown on the page.

Live at https://edwar281.github.io/public/ai-analyst/

**Author:** Will Edwards, PhD. Assisted by Claude.

## Architecture

```
browser (GitHub Pages) ──> Cloudflare Worker ──> Gemini API (free tier)
   │                        holds the API key
   └── Web Worker: Pyodide (Python + DuckDB in WebAssembly) with the Ookla tables
```

| Piece | Job |
|---|---|
| `index.html`, `app.js` | The page and the agent loop. It sends the conversation to the Worker, runs each step the model asks for, sends the results back, and streams every step to the screen. |
| `sandbox.js`, `runtime.py` | The code runner. A Web Worker loads Pyodide with DuckDB, pandas and matplotlib, puts the four tables in memory, and runs each SQL query or Python snippet in a fresh namespace. The page stops it and starts a new one if a step runs past 30 seconds. scipy and statsmodels load only when the code imports them. |
| `worker/worker.js` | A Cloudflare Worker that keeps the Gemini key off the page. It adds the system prompt and the two tool definitions, checks the request, applies a per-visitor limit, and only answers pages on the allowed origins. |
| `saved-runs.json` | Saved live runs of the six example questions, so a click replays instantly and costs nothing. |

The model has two tools: `run_sql` (one DuckDB query) and `run_python` (with `sql()`, `show()` and matplotlib). It sees up to 30 rows of each result plus any printed output, and gets errors back so it can fix its own code. Each question is capped at 8 steps.

Because the AI-written code runs in the visitor's own browser tab, no server ever executes it.

## Data

Built from the [Closing the Broadband Gap](../ookla-broadband/) pipeline: Speedtest by Ookla open data (594M tests, Q1 2019 to Q2 2026) assigned to counties and summarized by quarter.

| Table | Rows | Contents |
|---|---|---|
| `counties` | 3,233 | Population, land area, density, USDA rurality code, median household income |
| `county_quarterly` | 187,713 | Tests, median download and upload, latency, share meeting FCC 100/20, by county, quarter and network |
| `area_quarterly` | 300 | The same measures nationally and by rurality group |
| `disruptions` | 17 | County-quarters where test volume surged while speeds fell |

To rebuild: run `make panel` in `ookla-broadband/`, then `python data/build_data.py ../ookla-broadband data/parquet`.

## Setting it up

1. **Worker.** In the Cloudflare dashboard, create a Worker, replace its code with `worker/worker.js` and deploy. Under Settings > Variables and Secrets, add `GEMINI_API_KEY` as a secret (a key from [Google AI Studio](https://aistudio.google.com/apikey) in a project without billing stays on the free tier). Optional: `GEMINI_MODELS` (models to try in order, default `gemini-flash-latest,gemini-flash-lite-latest`), and `ALLOWED_ORIGINS` if the page moves to another domain.
   To deploy updates automatically instead, add `CLOUDFLARE_API_TOKEN` (the "Edit Cloudflare Workers" token template) and `CLOUDFLARE_ACCOUNT_ID` as GitHub repository secrets. The workflow then runs `wrangler deploy` with `worker/wrangler.jsonc` after the tests pass.
2. **Page.** Put the Worker's URL in `config.js`. GitHub Pages serves the folder as it is.
3. **Example answers.** In GitHub, Actions > ai-analyst > Run workflow runs the six example questions live and commits `saved-runs.json`.

To try it locally: `node tests/serve.mjs`, then open http://localhost:8000/.

## Tests

```bash
node --test tests/worker.test.mjs     # the Worker: CORS, validation, limits, error handling
node tests/e2e.mjs                    # a real browser: Pyodide, DuckDB, timeouts and the agent loop
```

The browser test uses a scripted stand-in for Gemini, so it needs no API key. GitHub Actions runs both on every change.

## Costs and limits

GitHub Pages, Cloudflare Workers and Gemini's free tier cost nothing. The Worker limits each visitor to 12 model calls a minute, and Gemini's free-tier quota caps the daily total. When Flash is busy or rate-limited, the Worker falls back to Flash-Lite, which has its own quota; when both are out, visitors are told to try the saved examples. On the free tier Google may use prompts to improve its products, which is acceptable here because everything sent is public data.

## Data license

Data: Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps, CC BY-NC-SA 4.0. USDA ERS Rural-Urban Continuum Codes and US Census data are public domain.
