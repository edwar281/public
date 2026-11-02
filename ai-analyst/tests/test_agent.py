from api.app import agent as agent_mod
from api.app.agent import Agent, scripted_chat, summarize_result


def fake_run(lang, code):
    if "boom" in code:
        return {"ok": False, "error": "Binder Error: boom", "stdout": "", "tables": [], "images": []}
    return {"ok": True, "stdout": "", "images": ["AAAA"], "tables": [
        {"title": None, "columns": ["a", "b"], "rows": [[1, 2.5]], "row_count": 1, "truncated": False}]}


def test_full_run_streams_steps_and_answer():
    chat = scripted_chat([
        {"note": "Checking the trend.", "calls": [{"lang": "sql", "purpose": "Get speeds", "code": "SELECT boom"}]},
        {"calls": [{"lang": "sql", "purpose": "Fixed query", "code": "SELECT 1"},
                   {"lang": "python", "purpose": "Chart it", "code": "plt.plot([1])"}]},
        {"answer": "Speeds tripled."},
    ])
    a = Agent(fake_run, chat_factory=lambda q: chat)
    events = list(a.run("How fast?"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "answer"
    assert kinds.count("step") == 3 and kinds.count("result") == 3
    answer = events[-1]
    assert answer["answer"] == "Speeds tripled."
    assert answer["trace"]["steps"] == 2 and answer["trace"]["llm_calls"] == 3
    assert len(answer["transcript"]) == 3
    # the model saw the error text, then the table and the chart count
    assert "ERROR" in chat.received[0][0] and "boom" in chat.received[0][0]
    assert "a,b" in chat.received[1][0] and "1 chart(s)" in chat.received[1][0]
    assert a.stats.snapshot()["answers"] == 1


def test_step_limit_forces_an_answer(monkeypatch):
    monkeypatch.setattr(agent_mod, "MAX_STEPS", 2)
    loop = {"calls": [{"lang": "sql", "purpose": "again", "code": "SELECT 1"}]}
    chat = scripted_chat([loop, loop, loop, {"answer": "Done."}])
    events = list(Agent(fake_run, chat_factory=lambda q: chat).run("q"))
    assert sum(e["type"] == "step" for e in events) == 2
    assert events[-1]["type"] == "answer"
    assert ["You're out of steps. Answer now with what you have."] in chat.received


def test_summaries_are_bounded():
    big = {"ok": True, "stdout": "x" * 50000, "tables": [], "images": []}
    assert len(summarize_result(big)) <= agent_mod.RESULT_CHARS
    rows = [[i, i * 2] for i in range(100)]
    s = summarize_result({"ok": True, "tables": [{"columns": ["i", "j"], "rows": rows, "row_count": 100}]})
    assert "first 30 shown" in s and "29,58" in s and "30,60" not in s
