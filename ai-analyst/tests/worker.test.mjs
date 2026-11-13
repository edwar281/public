// Unit tests for the Cloudflare Worker. Run: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { SYSTEM_PROMPT, validContents } from "../worker/worker.js";

const ORIGIN = "https://edwar281.github.io";
const env = { GEMINI_API_KEY: "test-key" };
let ipCounter = 0;

function chat(body, { origin = ORIGIN, ip } = {}) {
  return new Request("https://analyst.example.workers.dev/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: origin, "CF-Connecting-IP": ip || `10.0.0.${++ipCounter}` },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

function mockGemini(responder) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init, body: JSON.parse(init.body) });
    return responder(calls.at(-1));
  };
  return calls;
}

const okReply = () => new Response(JSON.stringify({
  candidates: [{ content: { role: "model", parts: [{ functionCall: { name: "run_sql", args: { purpose: "p", query: "select 1" } }, thoughtSignature: "sig" }] }, finishReason: "STOP" }],
  usageMetadata: { promptTokenCount: 100, candidatesTokenCount: 20, thoughtsTokenCount: 5 },
  modelVersion: "gemini-test",
}), { status: 200 });

const question = { contents: [{ role: "user", parts: [{ text: "How fast is rural internet?" }] }] };

test("health check reports whether a key is set", async () => {
  const res = await worker.fetch(new Request("https://x.workers.dev/", { headers: { Origin: ORIGIN } }), env);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, live: true });
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), ORIGIN);
  const off = await worker.fetch(new Request("https://x.workers.dev/"), {});
  assert.equal((await off.json()).live, false);
});

test("forwards the conversation with the system prompt, tools and key", async () => {
  const calls = mockGemini(okReply);
  const res = await worker.fetch(chat(question), env);
  assert.equal(res.status, 200);
  const out = await res.json();
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /models\/gemini-flash-latest:generateContent$/);
  assert.equal(calls[0].init.headers["x-goog-api-key"], "test-key");
  assert.equal(calls[0].body.systemInstruction.parts[0].text, SYSTEM_PROMPT);
  assert.deepEqual(calls[0].body.tools[0].functionDeclarations.map((t) => t.name), ["run_sql", "run_python"]);
  assert.deepEqual(calls[0].body.contents, question.contents);
  assert.equal(out.content.parts[0].thoughtSignature, "sig", "thought signatures must survive the round trip");
  assert.equal(out.input_tokens, 100);
  assert.equal(out.output_tokens, 25);
  assert.equal(out.model, "gemini-test");
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), ORIGIN);
});

test("uses GEMINI_MODEL when set", async () => {
  const calls = mockGemini(okReply);
  await worker.fetch(chat(question), { ...env, GEMINI_MODEL: "gemini-2.5-flash" });
  assert.match(calls[0].url, /models\/gemini-2\.5-flash:generateContent$/);
});

test("refuses other websites", async () => {
  const calls = mockGemini(okReply);
  const res = await worker.fetch(chat(question, { origin: "https://evil.example" }), env);
  assert.equal(res.status, 403);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), null);
  assert.equal(calls.length, 0);
});

test("ALLOWED_ORIGINS replaces the default list", async () => {
  mockGemini(okReply);
  const custom = { ...env, ALLOWED_ORIGINS: "https://willedwards.dev" };
  assert.equal((await worker.fetch(chat(question), custom)).status, 403);
  assert.equal((await worker.fetch(chat(question, { origin: "https://willedwards.dev" }), custom)).status, 200);
});

test("answers CORS preflight", async () => {
  const res = await worker.fetch(new Request("https://x.workers.dev/chat", { method: "OPTIONS", headers: { Origin: ORIGIN } }), env);
  assert.equal(res.status, 204);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), ORIGIN);
});

test("rejects malformed or oversized requests without calling Gemini", async () => {
  const calls = mockGemini(okReply);
  for (const bad of ["not json", {}, { contents: [] }, { contents: [{ role: "model", parts: [{ text: "hi" }] }] },
    { contents: [{ role: "user", parts: [{ inlineData: { mimeType: "image/png", data: "AAAA" } }] }] },
    { contents: [{ role: "system", parts: [{ text: "x" }] }] }]) {
    assert.equal((await worker.fetch(chat(bad), env)).status, 400, JSON.stringify(bad));
  }
  assert.equal((await worker.fetch(chat({ contents: [{ role: "user", parts: [{ text: "x".repeat(500_000) }] }] }), env)).status, 413);
  assert.equal(calls.length, 0);
});

test("needs a key", async () => {
  mockGemini(okReply);
  assert.equal((await worker.fetch(chat(question), {})).status, 503);
});

test("limits calls per visitor per minute", async () => {
  mockGemini(okReply);
  const statuses = [];
  for (let i = 0; i < 14; i++) statuses.push((await worker.fetch(chat(question, { ip: "10.9.9.9" }), env)).status);
  assert.deepEqual(statuses.slice(0, 12), Array(12).fill(200));
  assert.equal(statuses[12], 429);
});

test("explains Gemini's quota errors and hides other upstream errors", async () => {
  mockGemini(() => new Response("{}", { status: 429 }));
  const quota = await worker.fetch(chat(question), env);
  assert.equal(quota.status, 429);
  assert.match((await quota.json()).error, /free allowance/);
  const calls = mockGemini(() => new Response(JSON.stringify({ error: { message: "API key not valid." } }), { status: 400 }));
  const broken = await worker.fetch(chat(question), env);
  assert.equal(broken.status, 502);
  const body = await broken.json();
  assert.match(body.error, /went wrong/);
  assert.equal(body.detail, "Gemini 400: API key not valid.");
  assert.equal(calls.length, 1, "client errors are not retried");
});

test("retries when Gemini is busy", async () => {
  let n = 0;
  const calls = mockGemini(() => (++n < 3 ? new Response("{}", { status: 503 }) : okReply()));
  const res = await worker.fetch(chat(question), env);
  assert.equal(res.status, 200);
  assert.equal(calls.length, 3);
});

test("validContents accepts a normal tool round trip", () => {
  assert.ok(validContents([
    { role: "user", parts: [{ text: "q" }] },
    { role: "model", parts: [{ functionCall: { name: "run_sql", args: {} } }] },
    { role: "user", parts: [{ functionResponse: { name: "run_sql", response: { result: "1" } } }] },
  ]));
});
