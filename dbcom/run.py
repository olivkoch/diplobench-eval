"""Run a game with the diplobench.com protocol.

  python3 -m dbcom.run --out runs/test1 --default-model ministral-8b-latest \
      --model AUSTRIA=mistral-large-4 --stop-after S1901M
"""
import argparse
import logging

from . import protocol as P
from .game import Runner


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory (events.jsonl, game.json, memory.json, run.log)")
    ap.add_argument("--default-model", default="ministral-8b-latest")
    ap.add_argument("--model", action="append", default=[], help="POWER=model, repeatable")
    ap.add_argument("--stop-after", help="stop after this phase id, e.g. S1901M (for short tests)")
    args = ap.parse_args()

    models = {p: args.default_model for p in P.POWERS}
    for spec in args.model:
        power, model = spec.split("=", 1)
        assert power in P.POWERS, power
        models[power] = model

    runner = Runner(models, args.out, stop_after=args.stop_after)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(f"{args.out}/run.log")])
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    print(runner.run())


if __name__ == "__main__":
    main()
