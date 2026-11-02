import json

import runner
import runtime


def test_sql_returns_table(data_dir):
    runtime.connect(str(data_dir))
    r = json.loads(runtime.run("sql", "SELECT state, count(*) n FROM counties GROUP BY state"))
    assert r["ok"]
    assert r["tables"][0]["columns"] == ["state", "n"]
    assert r["tables"][0]["rows"] == [["CO", 2]]


def test_python_prints_shows_and_charts(data_dir):
    runtime.connect(str(data_dir))
    code = (
        "df = sql('SELECT quarter, median_download_mbps FROM area_quarterly ORDER BY quarter')\n"
        "print(round(df.median_download_mbps.iloc[-1] / df.median_download_mbps.iloc[0], 2))\n"
        "show(df, 'National trend')\n"
        "df.plot(x='quarter', y='median_download_mbps', title='Speed')\n"
    )
    r = json.loads(runtime.run("python", code))
    assert r["ok"], r["error"]
    assert r["stdout"].strip() == "3.69"
    assert r["tables"][0]["title"] == "National trend"
    assert len(r["images"]) == 1


def test_errors_come_back_as_text(data_dir):
    runtime.connect(str(data_dir))
    r = json.loads(runtime.run("sql", "SELECT nope FROM counties"))
    assert not r["ok"] and "nope" in r["error"]


def test_sql_cannot_read_other_files(data_dir):
    runtime.connect(str(data_dir))
    r = json.loads(runtime.run("sql", "SELECT * FROM read_csv('/etc/passwd')"))
    assert not r["ok"]


def test_runner_subprocess_round_trip(data_dir):
    r = runner.run_job("sql", "SELECT count(*) AS n FROM county_quarterly")
    assert r["ok"] and r["tables"][0]["rows"] == [[4]]


def test_runner_stops_runaway_code(data_dir, monkeypatch):
    monkeypatch.setattr(runner, "WALL_SECONDS", 3)
    r = runner.run_job("python", "while True:\n    pass\n")
    assert not r["ok"]
    assert "Stopped" in r["error"]


def test_runner_stops_memory_hog(data_dir):
    r = runner.run_job("python", "x = bytearray(4 * 1024**3)\n")
    assert not r["ok"]
