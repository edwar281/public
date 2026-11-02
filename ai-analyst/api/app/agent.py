"""The analyst agent: the model plans, the runner executes its code, results go back to the model.

AI-written code runs in the separate runner container, never in this process.
"""
import json
import threading
import time

from .llm import FakeChat, GeminiChat, ToolCall, Turn

MAX_STEPS = 8
RESULT_CHARS = 6000

SYSTEM_PROMPT = """You are a data analyst answering questions about US internet speeds, using Speedtest by Ookla open data.
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
  so print the key numbers.
  Each run starts fresh: variables don't carry over between steps. Label chart axes with units and give every chart a title.

## How to work
- Use at most 5 steps. Answer simple lookups with one query.
- If code errors, read the error and fix it.
- Then write the answer for a busy reader: lead with the finding and the numbers behind it in one or two
  sentences, add up to three short bullets if they help, and finish with one line on any caveat that
  matters. No headings. Don't mention tool names, and don't invent numbers you didn't compute.
- If the question is not about this data, say briefly what the data can answer instead."""


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.day = time.strftime("%Y-%m-%d")
        self.questions = self.answers = self.llm_calls = self.input_tokens = self.output_tokens = 0
        self.answer_seconds: list[float] = []

    def _roll(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.__init__()

    def add(self, **kw):
        with self.lock:
            self._roll()
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)

    def snapshot(self):
        with self.lock:
            self._roll()
            secs = sorted(self.answer_seconds)
            return {"day": self.day, "questions": self.questions, "answers": self.answers,
                    "llm_calls": self.llm_calls, "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "median_answer_seconds": round(secs[len(secs) // 2], 1) if secs else None}


def summarize_result(r: dict) -> str:
    """Compact text version of a browser result for the model."""
    if not isinstance(r, dict):
        return "No result."
    parts = []
    if not r.get("ok", False):
        parts.append(f"ERROR:\n{str(r.get('error'))[:2000]}")
    if r.get("stdout"):
        parts.append(f"Printed output:\n{str(r['stdout'])[:3000]}")
    for t in (r.get("tables") or [])[:3]:
        cols = [str(c) for c in t.get("columns", [])][:30]
        rows = (t.get("rows") or [])[:30]
        lines = [",".join(cols)] + [",".join("" if v is None else str(v) for v in row[:30]) for row in rows]
        title = f" '{t['title']}'" if t.get("title") else ""
        parts.append(f"Table{title}: {t.get('row_count', len(rows))} rows" +
                     (" (first 30 shown)" if t.get("row_count", 0) > 30 else "") + "\n" + "\n".join(lines))
    images = r.get("images")
    n_images = len(images) if isinstance(images, list) else int(images or 0)
    if n_images:
        parts.append(f"{n_images} chart(s) shown to the visitor.")
    return ("\n\n".join(parts) or "The code ran and produced no output.")[:RESULT_CHARS]


class Agent:
    def __init__(self, run_code, chat_factory=None):
        self.run_code = run_code  # (lang, code) -> result dict, executed by the runner service
        self.stats = Stats()
        self.chat_factory = chat_factory or (lambda q: GeminiChat(SYSTEM_PROMPT, q))

    def run(self, question: str, before_llm_call=None):
        """Answer one question, yielding events for the browser as the work happens."""
        started = time.time()
        chat = self.chat_factory(question)
        self.stats.add(questions=1)
        transcript, steps = [], 0
        trace = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "llm_ms": 0, "run_ms": 0, "model": ""}
        yield {"type": "start", "question": question}
        while True:
            if steps >= MAX_STEPS:
                chat.nudge("You're out of steps. Answer now with what you have.")
            if before_llm_call:
                before_llm_call()
            yield {"type": "thinking"}
            turn: Turn = chat.next_turn()
            trace["llm_calls"] += 1
            trace["input_tokens"] += turn.input_tokens
            trace["output_tokens"] += turn.output_tokens
            trace["llm_ms"] += turn.latency_ms
            trace["model"] = turn.model
            self.stats.add(llm_calls=1, input_tokens=turn.input_tokens, output_tokens=turn.output_tokens)
            log_line("llm", step=steps, ms=turn.latency_ms, input_tokens=turn.input_tokens,
                     output_tokens=turn.output_tokens, tool_calls=len(turn.calls), model=turn.model)

            calls = [c for c in turn.calls if c.name in ("run_sql", "run_python")][:3]
            if not calls or steps >= MAX_STEPS:
                break
            steps += 1
            if turn.text:
                yield {"type": "note", "text": turn.text}
            texts = []
            for i, c in enumerate(calls):
                call = _call_payload(i, c)
                yield {"type": "step", "step": steps, **call}
                t0 = time.monotonic()
                result = self.run_code(call["lang"], call["code"])
                ms = int((time.monotonic() - t0) * 1000)
                trace["run_ms"] += ms
                log_line("run", step=steps, lang=call["lang"], ok=result.get("ok"), ms=ms)
                yield {"type": "result", "step": steps, "id": i, "ms": ms, "result": result}
                transcript.append({**call, "step": steps, "result": result})
                texts.append(summarize_result(result))
            chat.add_results(texts)

        answer = turn.text or "I wasn't able to reach an answer. Try rephrasing the question."
        elapsed = round(time.time() - started, 1)
        with self.stats.lock:
            self.stats.answer_seconds.append(elapsed)
        self.stats.add(answers=1)
        trace.update(steps=steps, seconds=elapsed)
        log_line("answer", seconds=elapsed, steps=steps, llm_calls=trace["llm_calls"],
                 input_tokens=trace["input_tokens"], output_tokens=trace["output_tokens"])
        yield {"type": "answer", "answer": answer, "trace": trace, "transcript": transcript}


def _call_payload(i: int, c: ToolCall) -> dict:
    lang = "sql" if c.name == "run_sql" else "python"
    code = str(c.args.get("query") if lang == "sql" else c.args.get("code") or "")
    return {"id": i, "lang": lang, "purpose": str(c.args.get("purpose") or ""), "code": code}


def log_line(event: str, **kw):
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **kw}), flush=True)


def scripted_chat(script: list[dict]) -> FakeChat:
    """Build a FakeChat from a list of {"note", "calls": [{lang, purpose, code}]} or {"answer"} dicts."""
    turns = []
    for t in script:
        if "answer" in t:
            turns.append(Turn(text=t["answer"], calls=[], model="scripted-demo"))
        else:
            turns.append(Turn(text=t.get("note", ""), model="scripted-demo", calls=[
                ToolCall("run_sql" if c["lang"] == "sql" else "run_python",
                         {"purpose": c["purpose"], ("query" if c["lang"] == "sql" else "code"): c["code"]})
                for c in t["calls"]]))
    return FakeChat(script=turns)
