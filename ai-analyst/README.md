# Broadband Analyst

An AI analyst for seven years of US internet speed tests. Ask a question in plain English and it writes SQL and Python, runs them, reads the results and explains what it found. Every step, the code behind it, and the tokens and time it took are shown on the page.

**Author:** Will Edwards, PhD. Assisted by Claude.

## Architecture

```
browser ──HTTPS──> caddy ──> api (FastAPI) ──> Gemini API (free tier)
                                  │
                                  └──internal network──> runner (DuckDB, pandas, matplotlib)
                                                         no internet, read-only disk
```

| Container | Job |
|---|---|
| `caddy` | Public HTTPS with an automatic Let's Encrypt certificate. |
| `api` | Runs the agent loop with Gemini function calling and streams each step to the browser as newline-delimited JSON. Serves the page, applies per-visitor and daily limits, and logs every model call and code run as a JSON line. |
| `runner` | Executes the model's SQL and Python. It sits on an internal Docker network with no route out, runs as a non-root user on a read-only filesystem with all Linux capabilities dropped, and starts every job in a fresh subprocess with CPU, memory, file-size and wall-clock limits. |

The model has two tools: `run_sql` (one DuckDB query) and `run_python` (with `sql()`, `show()` and matplotlib). It sees up to 30 rows of each result plus any printed output, and gets errors back so it can fix its own code. Each question is capped at 8 steps.

The six example questions replay a saved live run, so a click costs nothing and appears at once. Visitors' own questions run live.

## Data

Built from the [Closing the Broadband Gap](../ookla-broadband/) pipeline: Speedtest by Ookla open data (594M tests, Q1 2019 to Q2 2026) assigned to counties and summarized by quarter.

| Table | Rows | Contents |
|---|---|---|
| `counties` | 3,233 | Population, land area, density, USDA rurality code, median household income |
| `county_quarterly` | 187,713 | Tests, median download and upload, latency, share meeting FCC 100/20, by county, quarter and network |
| `area_quarterly` | 300 | The same measures nationally and by rurality group |
| `disruptions` | 17 | County-quarters where test volume surged while speeds fell |

To rebuild: run `make panel` in `ookla-broadband/`, then `python data/build_data.py ../ookla-broadband data/parquet`.

## Running it

Locally, with a [Gemini API key](https://aistudio.google.com/apikey):

```bash
cp .env.example .env          # set GEMINI_API_KEY; DOMAIN=localhost works for testing
docker compose up --build
```

Without a key, the page still loads and the saved example answers still play.

On Oracle Cloud's Always Free tier (an Ampere A1 VM with Ubuntu, ports 80 and 443 open in the VCN security list):

```bash
curl -fsSL https://raw.githubusercontent.com/edwar281/public/main/ai-analyst/deploy/oracle-setup.sh | bash
nano ~/public/ai-analyst/.env       # DOMAIN and GEMINI_API_KEY
cd ~/public/ai-analyst && docker compose up -d --build
docker compose exec api python -m api.app.warm    # save the example answers
```

Pushes to `main` that touch `ai-analyst/` run the tests in GitHub Actions and, once the `ORACLE_HOST`, `ORACLE_USER` and `ORACLE_SSH_KEY` secrets are set, redeploy over SSH.

## Tests

```bash
pip install -r api/requirements.txt -r runner/requirements.txt pytest
python -m pytest tests
```

The tests use a scripted model, so they need no API key. They cover the agent loop, error recovery, the step cap, result truncation, rate limits, the daily model budget, saved-run replay, and the runner's timeout, memory limit and file-access lockout.

## Costs and limits

Hosting is free on Oracle's Always Free tier and the model runs on Gemini's free tier. Two settings in `.env` keep it inside the free quota: `QUESTIONS_PER_IP_PER_HOUR` (default 8) and `MAX_LLM_CALLS_PER_DAY` (default 200). On the free tier Google may use prompts to improve its products, which is acceptable here because everything sent is public data.

## Data license

Data: Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps, CC BY-NC-SA 4.0. USDA ERS Rural-Urban Continuum Codes and US Census data are public domain.
