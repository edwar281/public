// Where the page finds the Cloudflare Worker that holds the Gemini key.
// Leave workerUrl empty to switch live questions off; the saved example answers still play.
window.ANALYST_CONFIG = Object.assign({
  workerUrl: "https://broadband-analyst.edwar281.workers.dev",
  stepTimeoutMs: 30000,
}, window.ANALYST_CONFIG || {});
