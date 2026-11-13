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
  return py;
}

self.onmessage = async (ev) => {
  const { id, type, lang, code } = ev.data;
  try {
    if (!ready) ready = init();
    const py = await ready;
    if (type === "init") {
      self.postMessage({ id, ok: true });
      return;
    }
    if (lang === "python") {
      // Loads scipy, statsmodels and the like only when the code imports them.
      await py.loadPackagesFromImports(code, { messageCallback: () => {} });
    }
    self.postMessage({ id, result: JSON.parse(runFn(lang, code)) });
  } catch (err) {
    self.postMessage({ id, error: String(err && err.message ? err.message : err) });
  }
};
