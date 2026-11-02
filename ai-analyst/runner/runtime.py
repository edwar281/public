"""Runs one AI-written SQL query or Python snippet against the Ookla tables.

runner.py starts this file as a fresh subprocess for every job, with CPU, memory and
file limits, inside a container that has no network access and a read-only filesystem.
Usage: python -I runtime.py < job.json   (job = {"lang": "sql"|"python", "code": "..."})
"""
import base64
import os
import sys
import contextlib
import io
import json
import traceback

import duckdb
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TABLES = ("counties", "county_quarterly", "area_quarterly", "disruptions")
MAX_ROWS = 200
MAX_STDOUT = 8000
COLORS = ["#1f5f8b", "#d9822b", "#2e8b57", "#8e5ea2", "#c0392b", "#7f8c8d"]

plt.rcParams.update({
    "figure.figsize": (8, 4.5), "figure.dpi": 110, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.3, "font.size": 10,
    "axes.prop_cycle": matplotlib.cycler(color=COLORS),
})

con = None


def connect(data_dir):
    """Open an in-memory database with one table per parquet file, then lock it down."""
    global con
    con = duckdb.connect()
    con.execute("SET threads = 2")
    con.execute("SET memory_limit = '768MB'")
    for t in TABLES:
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_parquet('{data_dir}/{t}.parquet')")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")


def sql(query):
    """Run SQL against the Ookla tables and return a pandas DataFrame."""
    return con.execute(query).df()


def _table(df, title=None):
    df = pd.DataFrame(df).copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].round(4)
    rows = json.loads(df.head(MAX_ROWS).to_json(orient="values", date_format="iso"))
    return {"title": title, "columns": [str(c) for c in df.columns], "rows": rows,
            "row_count": int(len(df)), "truncated": len(df) > MAX_ROWS}


def run(lang, code):
    """Run one SQL query or Python snippet. Returns a JSON string."""
    tables = []
    result = {"ok": True, "stdout": "", "tables": [], "images": [], "error": None}

    def show(df, title=None):
        """Display a DataFrame as a table in the answer."""
        tables.append(_table(df, title))

    try:
        if lang == "sql":
            tables.append(_table(sql(code)))
        else:
            buf = io.StringIO()
            ns = {"sql": sql, "show": show, "pd": pd, "np": np, "plt": plt}
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(compile(code, "<analysis>", "exec"), ns)
            out = buf.getvalue()
            result["stdout"] = out[:MAX_STDOUT] + ("\n... (output truncated)" if len(out) > MAX_STDOUT else "")
            for num in plt.get_fignums()[:4]:
                png = io.BytesIO()
                plt.figure(num).savefig(png, format="png", bbox_inches="tight")
                result["images"].append(base64.b64encode(png.getvalue()).decode())
    except Exception as exc:  # the error goes back to the model so it can fix its code
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        result.update(ok=False, error="".join(tb[-2:])[-2000:])
    finally:
        plt.close("all")
    result["tables"] = tables[:4]
    return json.dumps(result)


if __name__ == "__main__":
    job = json.load(sys.stdin)
    connect(os.environ.get("DATA_DIR", "/data"))
    sys.stdout.write(run(job["lang"], job["code"]))
