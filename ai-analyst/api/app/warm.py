"""Run every example question live once and save the runs, so visitors' clicks replay instantly.

    docker compose exec api python -m api.app.warm            # only presets without a saved run
    docker compose exec api python -m api.app.warm --all      # refresh all of them
"""
import sys

from .main import cache, live_run
from .presets import PRESETS


def main():
    refresh = "--all" in sys.argv
    for p in PRESETS:
        if cache.get(p["id"]) and not refresh:
            print(f"skip  {p['id']} (saved {cache.get(p['id'])['recorded_at']})")
            continue
        last = None
        for event in live_run(p["question"], p["id"]):
            last = event
            if event["type"] == "step":
                print(f"      {p['id']} step {event['step']}: {event['purpose']}")
        status = "saved" if last and last["type"] == "answer" else f"FAILED: {last}"
        print(f"{status}  {p['id']}")


if __name__ == "__main__":
    main()
