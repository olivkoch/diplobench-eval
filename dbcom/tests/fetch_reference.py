"""Download published phase + trace files of a diplobench.com game (used by verify_prompts.py).

  python3 dbcom/tests/fetch_reference.py                 # c01-g01, S1901M..S1902M -> reference/c01-g01/
  python3 dbcom/tests/fetch_reference.py c01-g25 all     # every phase of c01-g25

File names on diplobench.com are content-hashed, so paths are resolved through
manifest.json -> matches -> the game's replay index.
"""
import json
import os
import sys

import requests

BASE = "https://diplobench.com/data/tournaments/openrouter-38-v2"
DEFAULT_PHASES = ["S1901M", "F1901M", "F1901R", "W1901A", "S1902M"]


def get(path):
    r = requests.get(f"{BASE}/{path}", timeout=60, headers={"User-Agent": "curl/8.0"})
    r.raise_for_status()
    return r.json()


def main():
    game_id = sys.argv[1] if len(sys.argv) > 1 else "c01-g01"
    wanted = None if len(sys.argv) > 2 and sys.argv[2] == "all" else DEFAULT_PHASES
    out = os.path.join(os.path.dirname(__file__), "..", "..", "reference", game_id)
    os.makedirs(out, exist_ok=True)

    matches = get(get("manifest.json")["files"]["matches"]["path"])
    game = next(g for g in matches["games"] if g["id"] == game_id)
    index = get(game["files"]["replayIndex"]["path"])
    for ph in index["phases"]:
        if wanted and ph["id"] not in wanted:
            continue
        for kind in ("phase", "traces"):
            with open(os.path.join(out, f"{ph['id']}.{kind}.json"), "w") as f:
                json.dump(get(ph["files"][kind]["path"]), f)
        print(ph["id"])
    print(f"saved to {os.path.normpath(out)}")


if __name__ == "__main__":
    main()
