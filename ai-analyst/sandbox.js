// Web Worker that runs the AI's SQL and Python in the visitor's browser.
// Pyodide (Python compiled to WebAssembly) loads DuckDB, pandas and matplotlib, then runtime.py,
// which holds the Ookla tables in memory. The page talks to this worker with postMessage only,
// and terminates it if a step runs too long.
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const TABLES = ["counties", "county_quarterly", "area_quarterly", "disruptions"];
const BASE_PACKAGES = ["duckdb", "numpy", "pandas", "matplotlib"];

// A module worker, so Pyodide loads with import() rather than importScripts().
const pyodideModule = import(`${PYODIDE_URL}pyodide.mjs`);

let ready = null;
let runFn = null;
let newSessionFn = null;

async function init() {
  const { loadPyodide } = await pyodideModule;
  const py = await loadPyodide({ indexURL: PYODIDE_URL });
  const [, runtime, ...files] = await Promise.all([
    py.loadPackage(BASE_PACKAGES, { messageCallback: () => {} }),
    fetch("runtime.py").then((r) => r.text()),
    ...TABLES.map((t) => fetch(`data/parquet/${t}.parquet`).then((r) => {
      if (!r.ok) throw new Error(`Couldn't load the ${t} table (${r.status}).`);
      return r.arrayBuffer();
    })),
  ]);
  py.FS.mkdirTree("/data");
  TABLES.forEach((t, i) => py.FS.writeFile(`/data/${t}.parquet`, new Uint8Array(files[i])));
  py.runPython(runtime);
  py.runPython("connect('/data')");
  runFn = py.globals.get("run");
  newSessionFn = py.globals.get("new_session");
  return py;
}

// Pure-Python packages that may come from PyPI when Pyodide doesn't ship them. Nothing else is installed.
const FROM_PYPI = new Set(["plotly", "narwhals"]);

async function loadMissing(py, name) {
  try {
    await py.loadPackage(name, { messageCallback: () => {}, errorCallback: () => {} });
    py.pyimport(name);
    return true;
  } catch {
    if (!FROM_PYPI.has(name)) return false;
  }
  try {
    await py.loadPackage("micropip", { messageCallback: () => {} });
    await py.pyimport("micropip").install(name);
    py.pyimport(name);
    return true;
  } catch {
    return false;
  }
}

self.onmessage = async (ev) => {
  const { id, type, lang, code } = ev.data;
  try {
    if (!ready) ready = init();
    const py = await ready;
    if (type === "init" || type === "new_session") {
      if (type === "new_session") newSessionFn();
      self.postMessage({ id, ok: true });
      return;
    }
    if (lang === "python") {
      // Loads scipy, statsmodels and the like only when the code imports them.
      await py.loadPackagesFromImports(code, { messageCallback: () => {} });
      if (/\bplotly\b/.test(code)) await loadMissing(py, "plotly");
    }
    let result = JSON.parse(runFn(lang, code));
    // Libraries such as pandas import scipy on their own (for example corr(method="spearman")),
    // which loadPackagesFromImports can't see. Load the missing package and try once more.
    const missing = !result.ok && /No module named '([\w.]+)'/.exec(result.error || "");
    if (missing && await loadMissing(py, missing[1].split(".")[0])) result = JSON.parse(runFn(lang, code));
    self.postMessage({ id, result });
  } catch (err) {
    self.postMessage({ id, error: String(err && err.message ? err.message : err) });
  }
};
