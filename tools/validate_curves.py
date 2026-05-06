#!/usr/bin/env python3
"""
Validate the proposed three-region auto-brightness curve against real
device data.

Replays a CSV of (timestamp, entity, value) rows -- as exported from
Home Assistant for this device -- and prints a side-by-side comparison
of the *current* curve in clock.yaml and the *proposed* three-region
curve (dark band 1-2-3 + log-linear lit ramp).

Usage:
    python3 tools/validate_curves.py /path/to/data.csv
"""

from __future__ import annotations

import csv
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Curves
# ---------------------------------------------------------------------------

FLOOR = 1.0 / 255.0


def current_curve(lux: float, bias: float = 1.0, sat_lux: float = 250.0,
                  max_b: float = 0.85) -> int:
    """Mirror of apply_auto_brightness in clock.yaml today."""
    if lux < 0.0:
        lux = 0.0
    ratio = min(lux / max(sat_lux, 1.0), 1.0)
    lit_bonus = (ratio ** 1.5) * (1.0 - FLOOR)
    t = min(FLOOR + lit_bonus * bias, max_b)
    return int(math.floor(t * 255.0 + 0.5))


def proposed_curve(lux: float, dim_lux: float = 5.0, bright_lux: float = 200.0,
                   offset_stops: float = 0.0, max_b: float = 0.85) -> int:
    """
    Three-region curve. Mirrors apply_auto_brightness in clock.yaml.

      Dark band   : lux in [0, dim_lux]            B in {1, 2, 3} log-quantised
      Lit ramp    : lux in [dim_lux, bright_lux]   B = 3 .. 255, linear in log(lux)
      Saturated   : lux >= bright_lux              B = 255 (then capped by max_b)

    The dark band is invariant to offset_stops -- the napping-room floor is
    sacred. offset_stops only multiplies the lit-ramp output, which is then
    clamped to be at least B=3 so a -2 stop trim cannot pull the lit ramp
    below the top of the dark band.
    """
    if lux < 0.0:
        lux = 0.0
    if dim_lux < 0.5:
        dim_lux = 0.5
    if bright_lux <= dim_lux:
        bright_lux = dim_lux + 1.0

    eps = 1e-3
    b3 = 3.0 / 255.0

    if lux <= dim_lux:
        night_lux = dim_lux / 10.0
        lo = math.log(night_lux + eps)
        hi = math.log(dim_lux + eps)
        t = (math.log(lux + eps) - lo) / (hi - lo)
        t = max(0.0, min(1.0, t))
        step = min(2, int(t * 3.0))
        target = (1.0 + step) / 255.0
    else:
        if lux >= bright_lux:
            t = 1.0
        else:
            t = (math.log(lux) - math.log(dim_lux)) / (
                math.log(bright_lux) - math.log(dim_lux)
            )
        target = b3 + t * (1.0 - b3)
        target *= (2.0 ** offset_stops)
        if target < b3:
            target = b3

    target = min(target, max_b)
    return int(math.floor(target * 255.0 + 0.5))


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_lux(path: Path) -> list[tuple[datetime, float]]:
    out = []
    for r in csv.DictReader(path.open()):
        if r["entity"] != "ambient_light_lux":
            continue
        ts = datetime.fromisoformat(r["timestamp"])
        out.append((ts, float(r["value"])))
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def histogram(label: str, B_series: list[int]) -> None:
    """Print a coarse histogram across the B=0..255 range."""
    n = len(B_series)
    bins = [
        ("B=1   (1 LED  PWM 1)", lambda B: B == 1),
        ("B=2   (2 LEDs PWM 1)", lambda B: B == 2),
        ("B=3   (3 LEDs PWM 1)", lambda B: B == 3),
        ("B 4-15   (very dim)",   lambda B: 4  <= B <= 15),
        ("B 16-63  (dim)",        lambda B: 16 <= B <= 63),
        ("B 64-127 (medium)",     lambda B: 64 <= B <= 127),
        ("B 128-216 (bright)",    lambda B: 128 <= B <= 216),
        ("B 217-255 (max-cap)",   lambda B: B >= 217),
    ]
    print(f"\n{label}  (N={n})")
    for name, pred in bins:
        c = sum(1 for B in B_series if pred(B))
        pct = 100.0 * c / n
        bar = "#" * int(pct / 2)
        print(f"  {name:24s}  {c:4d}  {pct:5.1f}%  {bar}")
    avg = sum(B_series) / n
    print(f"  mean B = {avg:.1f}")


def step_changes(B_series: list[int]) -> dict:
    """How jumpy is the brightness? Larger jumps = more visible flicker."""
    diffs = [abs(B_series[i] - B_series[i-1]) for i in range(1, len(B_series))]
    if not diffs:
        return {}
    diffs_sorted = sorted(diffs)
    return {
        "mean": sum(diffs) / len(diffs),
        "p50":  diffs_sorted[len(diffs)//2],
        "p95":  diffs_sorted[int(len(diffs)*0.95)],
        "max":  max(diffs),
        "n_big_steps": sum(1 for d in diffs if d >= 20),
    }


def lux_band_summary(samples: list[tuple[datetime, float]]) -> None:
    lux = [v for _, v in samples]
    bands = [
        ("very dark      (<1 lux)",        lambda x: x < 1),
        ("dark           (1..5 lux)",      lambda x: 1 <= x < 5),
        ("dim evening    (5..20 lux)",     lambda x: 5 <= x < 20),
        ("dim daytime    (20..60 lux)",    lambda x: 20 <= x < 60),
        ("normal indoor  (60..200 lux)",   lambda x: 60 <= x < 200),
        ("bright indoor  (200..500 lux)",  lambda x: 200 <= x < 500),
        ("very bright    (500..1500)",     lambda x: 500 <= x < 1500),
        ("direct sun     (>=1500)",        lambda x: x >= 1500),
    ]
    print(f"\nLux distribution  (N={len(lux)})")
    n = len(lux)
    for name, pred in bands:
        c = sum(1 for x in lux if pred(x))
        pct = 100.0 * c / n
        bar = "#" * int(pct / 2)
        print(f"  {name:32s}  {c:4d}  {pct:5.1f}%  {bar}")


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <path-to-data.csv>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    samples = load_lux(path)
    if not samples:
        print("no ambient_light_lux rows found", file=sys.stderr)
        return 1

    span_h = (samples[-1][0] - samples[0][0]).total_seconds() / 3600.0
    print(f"loaded {len(samples)} lux samples over {span_h:.1f} h")
    print(f"  {samples[0][0].isoformat()}  ..  {samples[-1][0].isoformat()}")

    lux_band_summary(samples)

    lux = [v for _, v in samples]

    # Current curve, defaults that match clock.yaml today
    cur = [current_curve(x, bias=1.0, sat_lux=250.0, max_b=0.85) for x in lux]
    histogram("CURRENT  (bias=1.0, sat=250, max=0.85)", cur)
    print(f"  step jumps: {step_changes(cur)}")

    # Current curve, the bias the user actually settled on by end of day
    cur_b27 = [current_curve(x, bias=2.7, sat_lux=250.0, max_b=0.85) for x in lux]
    histogram("CURRENT  (bias=2.7, sat=250, max=0.85)  -- end-of-day setting", cur_b27)

    # Proposed, defaults
    new = [proposed_curve(x, dim_lux=5.0, bright_lux=200.0,
                          offset_stops=0.0, max_b=0.85) for x in lux]
    histogram("PROPOSED (dim=5, bright=200, offset=0, max=0.85)", new)
    print(f"  step jumps: {step_changes(new)}")

    # Proposed, slightly tuned for this room
    new_t = [proposed_curve(x, dim_lux=5.0, bright_lux=300.0,
                            offset_stops=0.0, max_b=0.85) for x in lux]
    histogram("PROPOSED (dim=5, bright=300, offset=0, max=0.85)  -- bright_lux up", new_t)

    # Proposed, with offset = +0.5 stops (everywhere brighter except dark band)
    new_o = [proposed_curve(x, dim_lux=5.0, bright_lux=200.0,
                            offset_stops=0.5, max_b=0.85) for x in lux]
    histogram("PROPOSED (dim=5, bright=200, offset=+0.5, max=0.85)", new_o)

    return 0


if __name__ == "__main__":
    sys.exit(main())
