"""Starter questions, plus saved runs so a click costs nothing and appears instantly."""
import json
import os
import threading
import time

PRESETS = [
    {"id": "rural-gap", "label": "Rural vs. big-city speeds",
     "question": "How much faster has home internet gotten in rural areas since 2019, compared with large metros? Chart the trend."},
    {"id": "most-improved", "label": "Most improved counties",
     "question": "Which 10 counties with at least 10,000 people improved the most in median home download speed between 2019 and the last four quarters?"},
    {"id": "income", "label": "Does income matter?",
     "question": "Is county household income related to home broadband speed? Show a scatter plot with a trend line and tell me how strong the relationship is."},
    {"id": "below-benchmark", "label": "States left behind",
     "question": "In the latest quarter, which states have the most people living in counties where fewer than half of home speed tests meet the FCC 100/20 benchmark?"},
    {"id": "helene", "label": "Hurricane Helene",
     "question": "Show what happened to home internet in Buncombe County, NC around Hurricane Helene in late 2024."},
    {"id": "mobile-vs-fixed", "label": "Mobile catching up?",
     "question": "Compare national median download speeds on mobile and home broadband since 2019. Is mobile catching up?"},
]
BY_ID = {p["id"]: p for p in PRESETS}


class PresetCache:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        if os.path.exists(path):
            with open(path) as f:
                self.data = json.load(f)

    def get(self, preset_id: str):
        return self.data.get(preset_id)

    def save(self, preset_id: str, transcript: list, answer: str, trace: dict):
        with self.lock:
            self.data[preset_id] = {"question": BY_ID[preset_id]["question"], "transcript": transcript,
                                    "answer": answer, "trace": trace,
                                    "recorded_at": time.strftime("%Y-%m-%d")}
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)
