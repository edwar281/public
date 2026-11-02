import importlib
import json

import pytest
from fastapi.testclient import TestClient

from api.app.agent import Agent, scripted_chat

SCRIPT = [
    {"calls": [{"lang": "sql", "purpose": "Look it up", "code": "SELECT 1"}]},
    {"answer": "Rural speeds rose about tenfold."},
]


def fake_run(lang, code):
    return {"ok": True, "stdout": "", "images": [], "tables": [
        {"title": None, "columns": ["x"], "rows": [[1]], "row_count": 1, "truncated": False}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "presets.json"))
    monkeypatch.setenv("QUESTIONS_PER_IP_PER_HOUR", "2")
    from api.app import main
    main = importlib.reload(main)
    main.agent = Agent(fake_run, chat_factory=lambda q: scripted_chat([dict(s) for s in SCRIPT]))
    return TestClient(main.app), main


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_config_lists_presets(client):
    c, _ = client
    cfg = c.get("/api/config").json()
    assert cfg["live"] is True and len(cfg["presets"]) == 6
    assert all(p["saved"] is None for p in cfg["presets"])


def test_preset_runs_live_once_then_replays(client):
    c, main = client
    first = events(c.post("/api/ask", json={"preset_id": "rural-gap"}))
    assert first[-1]["type"] == "answer" and "transcript" not in first[-1]
    assert c.get("/api/config").json()["presets"][0]["saved"]
    # the replay makes no model calls, so it works even with no live questions left
    main.LIVE = False
    again = events(c.post("/api/ask", json={"preset_id": "rural-gap"}))
    assert [e["type"] for e in again] == ["start", "step", "result", "answer"]
    assert again[-1]["saved"] and again[-1]["answer"] == "Rural speeds rose about tenfold."


def test_rate_limit_per_visitor(client):
    c, _ = client
    for _ in range(2):
        assert c.post("/api/ask", json={"question": "How fast is Denver?"}).status_code == 200
    r = c.post("/api/ask", json={"question": "One more?"})
    assert r.status_code == 429 and "limit" in r.json()["detail"]


def test_daily_model_budget(client, monkeypatch):
    c, main = client
    monkeypatch.setattr(main, "LLM_CALLS_PER_DAY", 1)
    out = events(c.post("/api/ask", json={"question": "How fast is Denver?"}))
    assert out[-1]["type"] == "error" and "free allowance" in out[-1]["message"]


def test_rejects_empty_and_long_questions(client):
    c, _ = client
    assert c.post("/api/ask", json={"question": "  "}).status_code == 400
    assert c.post("/api/ask", json={"question": "x" * 501}).status_code == 422


def test_serves_page(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "Ask the broadband data" in r.text
    assert c.get("/app.js").status_code == 200
