"""HTTP API and web page for the Ookla analyst."""
import json
import os
import threading
import time
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent, log_line
from .presets import BY_ID, PRESETS, PresetCache

WEB_DIR = os.environ.get("WEB_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "web"))
RUNNER_URL = os.environ.get("RUNNER_URL", "http://runner:8001")
CACHE_PATH = os.environ.get("CACHE_PATH", "/cache/presets.json")
QUESTIONS_PER_HOUR = int(os.environ.get("QUESTIONS_PER_IP_PER_HOUR", "8"))
LLM_CALLS_PER_DAY = int(os.environ.get("MAX_LLM_CALLS_PER_DAY", "200"))
LIVE = bool(os.environ.get("GEMINI_API_KEY"))


class QuotaExceeded(Exception):
    pass


class Limits:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_ip: dict[str, deque] = defaultdict(deque)
        self.day = time.strftime("%Y-%m-%d")
        self.calls_today = 0

    def allow_question(self, ip: str) -> bool:
        now = time.time()
        with self.lock:
            q = self.by_ip[ip]
            while q and q[0] < now - 3600:
                q.popleft()
            if len(q) >= QUESTIONS_PER_HOUR:
                return False
            q.append(now)
            return True

    def take_llm_call(self):
        with self.lock:
            today = time.strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.calls_today = today, 0
            if self.calls_today >= LLM_CALLS_PER_DAY:
                raise QuotaExceeded()
            self.calls_today += 1


def run_code(lang: str, code: str) -> dict:
    try:
        r = httpx.post(f"{RUNNER_URL}/run", json={"lang": lang, "code": code}, timeout=60)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"The code runner didn't respond ({type(exc).__name__}).",
                "stdout": "", "tables": [], "images": []}


app = FastAPI(title="Ookla analyst", docs_url=None, redoc_url=None, openapi_url=None)
agent = Agent(run_code)
cache = PresetCache(CACHE_PATH)
limits = Limits()


def client_ip(request: Request) -> str:
    # Caddy appends the real client address as the last X-Forwarded-For entry.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "unknown")


class Ask(BaseModel):
    question: str | None = Field(default=None, max_length=500)
    preset_id: str | None = None
    live: bool = False


def ndjson(events):
    for e in events:
        yield json.dumps(e) + "\n"


def replay(saved: dict):
    yield {"type": "start", "question": saved["question"], "saved": saved["recorded_at"]}
    for s in saved["transcript"]:
        yield {"type": "step", **{k: s[k] for k in ("step", "id", "lang", "purpose", "code")}}
        yield {"type": "result", "step": s["step"], "id": s["id"], "result": s["result"]}
    yield {"type": "answer", "answer": saved["answer"], "trace": saved["trace"], "saved": saved["recorded_at"]}


def live_run(question: str, preset_id: str | None):
    def gate():
        limits.take_llm_call()

    try:
        for event in agent.run(question, before_llm_call=gate):
            if event["type"] == "answer":
                if preset_id:
                    cache.save(preset_id, event["transcript"], event["answer"], event["trace"])
                event = {k: v for k, v in event.items() if k != "transcript"}
            yield event
    except QuotaExceeded:
        yield {"type": "error", "message": "Today's free allowance for live questions is used up. "
                                           "The example questions still work, and live questions reset tomorrow."}
    except Exception as exc:  # report model or network trouble to the visitor instead of hanging
        log_line("error", kind=type(exc).__name__, detail=str(exc)[:300])
        yield {"type": "error", "message": "Something went wrong talking to the AI model. Please try again shortly."}


@app.get("/api/config")
def config():
    return {"live": LIVE, "presets": [{**p, "saved": (cache.get(p["id"]) or {}).get("recorded_at")} for p in PRESETS]}


@app.post("/api/ask")
def ask(body: Ask, request: Request):
    preset = BY_ID.get(body.preset_id or "")
    if preset and not body.live and cache.get(preset["id"]):
        log_line("replay", preset=preset["id"])
        return StreamingResponse(ndjson(replay(cache.get(preset["id"]))), media_type="application/x-ndjson")
    question = preset["question"] if preset else (body.question or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question about US internet speeds.")
    if not LIVE:
        raise HTTPException(503, "Live questions are switched off right now. The example questions still work.")
    if not limits.allow_question(client_ip(request)):
        raise HTTPException(429, f"You've asked {QUESTIONS_PER_HOUR} live questions this hour, the limit for this "
                                 "free demo. The example questions still work.")
    log_line("question", preset=preset["id"] if preset else None, chars=len(question))
    return StreamingResponse(ndjson(live_run(question, preset["id"] if preset else None)),
                             media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@app.get("/api/stats")
def stats():
    return agent.stats.snapshot()


@app.get("/api/health")
def health():
    return {"ok": True, "live": LIVE}


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
