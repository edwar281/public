"""Model access. Gemini on Google's free tier by default; a scripted fake for tests and offline demos."""
import os
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

TOOLS = [
    {
        "name": "run_sql",
        "description": "Run one DuckDB SQL query against the Ookla tables. Returns the columns and up to 30 rows, "
                       "and the visitor sees the full result table. Use it to look up and aggregate data.",
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string", "description": "One short sentence, shown to the visitor, saying what this step does."},
                "query": {"type": "string", "description": "A single DuckDB SELECT or WITH query."},
            },
            "required": ["purpose", "query"],
        },
    },
    {
        "name": "run_python",
        "description": "Run Python (pandas, numpy, matplotlib, scipy, statsmodels) in a sandbox. "
                       "sql(query) returns a DataFrame, show(df, title) displays a table, and any matplotlib "
                       "figure is shown as a chart. Printed output comes back to you. Each run starts fresh. Use it for charts, "
                       "statistics and anything SQL can't do easily.",
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string", "description": "One short sentence, shown to the visitor, saying what this step does."},
                "code": {"type": "string", "description": "Python code. Print the numbers you need to see."},
            },
            "required": ["purpose", "code"],
        },
    },
]


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    calls: list[ToolCall]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    model: str = ""


class GeminiChat:
    """One conversation with Gemini, with our two tools and no automatic function calling."""

    def __init__(self, system_prompt: str, question: str, client=None, model=None):
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
        self.client = client or genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in TOOLS])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=8192,
        )
        self.contents = [types.Content(role="user", parts=[types.Part.from_text(text=question)])]
        self.pending: list[ToolCall] = []

    def next_turn(self) -> Turn:
        start = time.monotonic()
        resp = self.client.models.generate_content(model=self.model, contents=self.contents, config=self.config)
        latency = int((time.monotonic() - start) * 1000)
        content = resp.candidates[0].content if resp.candidates else None
        if content is not None:
            self.contents.append(content)  # keeps thought signatures intact for the next request
        calls = [ToolCall(c.name, dict(c.args or {})) for c in (resp.function_calls or [])]
        text = "".join(p.text for p in (content.parts if content and content.parts else [])
                       if getattr(p, "text", None) and not getattr(p, "thought", False))
        usage = resp.usage_metadata
        self.pending = calls
        return Turn(text=text.strip(), calls=calls, latency_ms=latency, model=self.model,
                    input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                    output_tokens=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0)

    def add_results(self, results: list[str]):
        parts = [types.Part.from_function_response(name=call.name, response={"result": text})
                 for call, text in zip(self.pending, results)]
        self.contents.append(types.Content(role="user", parts=parts))
        self.pending = []

    def nudge(self, text: str):
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))


@dataclass
class FakeChat:
    """Plays back scripted turns. Used by the tests and by DEMO_MODE when no API key is set."""

    script: list[Turn] = field(default_factory=list)
    received: list[list[str]] = field(default_factory=list)
    model: str = "scripted-demo"

    def next_turn(self) -> Turn:
        if not self.script:
            return Turn(text="I couldn't finish this one. Try one of the example questions.", calls=[], model=self.model)
        return self.script.pop(0)

    def add_results(self, results: list[str]):
        self.received.append(results)

    def nudge(self, text: str):
        self.received.append([text])
