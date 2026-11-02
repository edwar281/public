"""Code runner service for the analyst.

Only the api container can reach it: it sits on an internal Docker network with no
route to the internet. Each job runs in a fresh subprocess with hard CPU, memory,
file-size and open-file limits and a wall-clock timeout, so a runaway query can't
take the service down or leave state behind for the next visitor.
"""
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import threading

from fastapi import FastAPI
from pydantic import BaseModel, Field

WALL_SECONDS = int(os.environ.get("RUN_TIMEOUT_SECONDS", "30"))
MEMORY_BYTES = int(os.environ.get("RUN_MEMORY_MB", "1536")) * 1024 * 1024
RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime.py")
# In the container, kill every leftover process after each job (forked children, setsid escapes).
# Off by default so the tests never touch processes on a developer's machine.
REAP = os.environ.get("RUNNER_REAP") == "1"
LOCK = threading.Lock()  # one job at a time, so reaping can't hit another visitor's job

app = FastAPI(title="analyst-runner", docs_url=None, redoc_url=None, openapi_url=None)


class Job(BaseModel):
    lang: str = Field(pattern="^(sql|python)$")
    code: str = Field(max_length=20000)


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (WALL_SECONDS, WALL_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024, 20 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def failure(message: str) -> dict:
    return {"ok": False, "error": message, "stdout": "", "tables": [], "images": []}


def _reap_strays():
    """Kill every process in the container except PID 1 (tini) and this server."""
    keep = {1, os.getpid()}
    for name in os.listdir("/proc"):
        if name.isdigit() and int(name) not in keep:
            try:
                os.kill(int(name), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def run_job(lang: str, code: str) -> dict:
    with LOCK, tempfile.TemporaryDirectory() as tmp:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "MPLCONFIGDIR": tmp,
               "DATA_DIR": os.environ.get("DATA_DIR", "/data"), "OPENBLAS_NUM_THREADS": "2"}
        proc = subprocess.Popen([sys.executable, "-I", RUNTIME], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=tmp, env=env,
                                preexec_fn=_limits, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(json.dumps({"lang": lang, "code": code}), timeout=WALL_SECONDS)
        except subprocess.TimeoutExpired:
            return failure(f"Stopped after {WALL_SECONDS} seconds. Try a smaller query.")
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # the job and anything it started
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
            if REAP:
                _reap_strays()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        if proc.returncode and proc.returncode < 0:
            return failure("Stopped because it used too much time or memory.")
        return failure((stderr or "The code exited without a result.")[-1500:])


@app.post("/run")
def run(job: Job):
    return run_job(job.lang, job.code)


@app.get("/health")
def health():
    return {"ok": True}
