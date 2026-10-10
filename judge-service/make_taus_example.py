#!/usr/bin/env python3
"""taus.example.json generator for judge-service causal metrics (pehe / ate_bias).

WHY THIS EXISTS
---------------
judge v0.2 added set-level causal metrics `pehe` and `ate_bias`. Those metrics
need ground-truth individual treatment effects

    tau_i = Y_i(1) - Y_i(0)

for every example. That tau is NOT the classification label -- it is a
continuous number supplied separately via the `JUDGE_TAUS_PATH` env var at
judge startup (see store.load_dataset). This script generates a matching
`taus.json` from a labels file so coders have a fixture to integrate against.

IMPORTANT -- SYNTHETIC
----------------------
The values produced here are SYNTHETIC (seeded Gaussian noise around 0). They
are NOT derived from the labels and NOT real treatment effects. They exist
only so a coder can:
  * confirm judge loads taus and that pehe/ate_bias are wired end-to-end,
  * plug in their own tau_hat estimates and watch PEHE move away from 0.

For real evaluation you must supply the true tau from your experiment.

USAGE
-----
  python make_taus_example.py                               # writes taus.example.json beside labels.example.json
  python make_taus_example.py --labels L.json --out T.json --seed 42 --scale 0.5
  python make_taus_example.py --print-only                 # just print, don't write

LIBRARY
-------
  from make_taus_example import make_taus
  taus = make_taus(labels_dict, seed=42, scale=0.5)         # -> {example_id: float}
"""
from __future__ import annotations

import argparse
import json
import os
import random
from typing import Any

DEFAULT_SCALE = 0.5
DEFAULT_SEED = 42
_HERE = os.path.dirname(os.path.abspath(__file__))


def make_taus(
    labels: dict[str, Any],
    seed: int = DEFAULT_SEED,
    scale: float = DEFAULT_SCALE,
) -> dict[str, float]:
    """Return {example_id: tau_float} aligned to `labels` keys (sorted).

    Synthetic: tau_i = round(gaussian(0, scale), 4) with a fixed seed, so the
    fixture is reproducible across runs.
    """
    rng = random.Random(seed)
    return {
        ex_id: round(rng.gauss(0.0, scale), 4)
        for ex_id in sorted(labels.keys())
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a synthetic taus.json fixture for judge causal metrics."
    )
    ap.add_argument(
        "--labels",
        default=os.path.join(_HERE, "labels.example.json"),
        help="labels json ({example_id: label}); output keys mirror its keys",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(_HERE, "taus.example.json"),
        help="output taus json path",
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument(
        "--scale", type=float, default=DEFAULT_SCALE,
        help="std-dev of the synthetic Gaussian tau",
    )
    ap.add_argument(
        "--print-only", action="store_true",
        help="print to stdout, do not write file",
    )
    args = ap.parse_args()

    with open(args.labels, "r", encoding="utf-8") as f:
        labels = json.load(f)

    taus = make_taus(labels, seed=args.seed, scale=args.scale)

    if args.print_only:
        print(json.dumps(taus, indent=2, ensure_ascii=False))
        return 0

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(taus, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"[make_taus] wrote {len(taus)} tau entries -> {args.out}")
    print(f"[make_taus] seed={args.seed} scale={args.scale} (SYNTHETIC fixture)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
