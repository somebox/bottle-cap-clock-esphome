#!/usr/bin/env python3
"""
Verify the brightness mapping pipeline used in clock.yaml.

The firmware does brightness in three stages:

  1. apply_auto_brightness script: lux (+ user bias, capped by max)
     produces a float target in 0..1 that is fed to the light's
     set_brightness().
  2. Display lambda: HA hands us back a float user_brightness in
     0..1; we round to an integer B in 0..255 and translate it to
     a max-channel level X with a small ladder at the dim end.
  3. paint_cap: turns FC.max = X into (n_leds_lit, per_led_pwm).

This script ports each stage to Python, then asserts the contracts
we documented (and informally tested) in chat:

  - lux=0 always lands at B=1, regardless of bias.
  - bias only scales the lit-room bonus, never the floor.
  - lux=3 is the seam between the linear and log regimes; the
    curve must be continuous there.
  - B ∈ {1,2,3} drives the cap with 1, 2, 3 LEDs at PWM 1.
  - B ≥ 4 drives all 3 LEDs at PWM B-2.
  - Max-brightness cap clamps the *target*, not B directly.

Run as:  python3 tests/test_brightness_mapping.py
Exits non-zero on the first failed assertion.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Stage 1: apply_auto_brightness
# ---------------------------------------------------------------------------

FLOOR = 1.0 / 255.0
CURVE_P = 1.5


def auto_brightness_target(
    lux: float,
    bias: float = 1.0,
    max_b: float = 1.0,
    sat_lux: float = 250.0,
) -> float:
    """Mirror of the apply_auto_brightness lambda in clock.yaml."""
    if lux < 0.0:
        lux = 0.0
    sat = sat_lux if sat_lux >= 1.0 else 1.0

    ratio = lux / sat
    if ratio > 1.0:
        ratio = 1.0

    lit_bonus = (ratio ** CURVE_P) * (1.0 - FLOOR)
    target = FLOOR + lit_bonus * bias
    if target > max_b:
        target = max_b
    return target


# ---------------------------------------------------------------------------
# Stage 2: user_brightness (0..1 float) -> B (0..255 int) -> X (max channel)
# ---------------------------------------------------------------------------

def brightness_to_B(user_brightness: float) -> int:
    """ESPHome stores brightness as a float; HA exposes it 0..255."""
    # roundf in C rounds half away from zero.
    return int(math.floor(user_brightness * 255.0 + 0.5))


def B_to_X(B: int) -> float:
    """Display lambda's B -> X mapping."""
    if B <= 0:
        return 0.0
    if B <= 3:
        return B / 3.0
    return float(B - 2)


# ---------------------------------------------------------------------------
# Stage 3: paint_cap
# ---------------------------------------------------------------------------

@dataclass
class CapOutput:
    n_leds: int       # how many of the 3 LEDs in the cap are lit
    pwm: int          # per-LED PWM (the max channel value, 0..255)


def paint_cap_white(X: float) -> CapOutput:
    """
    What paint_cap produces for a white pixel (FC = X, X, X).
    `pwm` is what the max channel ends up as in the on_c color.
    """
    LEDS_PER_CAP = 3
    max_c = X
    if max_c < 0.167:
        n = 0
        scale = 0.0
    elif max_c >= 1.0:
        n = LEDS_PER_CAP
        scale = 1.0
    else:
        n = int(round(max_c * LEDS_PER_CAP))
        if n < 1:
            n = 1
        if n > LEDS_PER_CAP:
            n = LEDS_PER_CAP
        scale = 1.0 / max_c

    pwm = min(255, int(X * scale))
    return CapOutput(n_leds=n, pwm=pwm)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def end_to_end(
    lux: float,
    bias: float = 1.0,
    max_b: float = 1.0,
    sat_lux: float = 250.0,
) -> int:
    """Full pipeline lux -> HA brightness B."""
    target = auto_brightness_target(lux, bias, max_b, sat_lux)
    return brightness_to_B(target)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

FAILURES: list[str] = []


def check(label: str, got, want):
    if got != want:
        FAILURES.append(f"  {label}: got {got!r}, want {want!r}")
        print(f"FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"ok    {label}: {got!r}")


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


# --- Stage 3: paint_cap ladder -------------------------------------------

print("\n# paint_cap ladder (white pixel)")
# B=1..3 -> 1, 2, 3 LEDs at PWM 1
check("B=1  -> (1 LED, PWM 1)", paint_cap_white(B_to_X(1)),
      CapOutput(1, 1))
check("B=2  -> (2 LEDs, PWM 1)", paint_cap_white(B_to_X(2)),
      CapOutput(2, 1))
check("B=3  -> (3 LEDs, PWM 1)", paint_cap_white(B_to_X(3)),
      CapOutput(3, 1))
# B>=4 -> 3 LEDs at PWM B-2
for B in (4, 5, 10, 50, 128, 255):
    check(f"B={B:<3d} -> (3 LEDs, PWM {B-2})",
          paint_cap_white(B_to_X(B)),
          CapOutput(3, B - 2))
check("B=0  -> all off", paint_cap_white(B_to_X(0)),
      CapOutput(0, 0))


# --- Stage 1: dark-room floor is bias-invariant --------------------------

print("\n# Dark-room floor (lux=0 always -> B=1, regardless of bias)")
for bias in (0.5, 1.0, 1.5, 2.0, 3.0):
    check(f"lux=0, bias={bias} -> B=1", end_to_end(0.0, bias=bias), 1)


# --- Stage 1: curve anchors at sat=250, bias=1.0, max=1.0 ----------------

print("\n# Auto curve anchors (saturation_lux=250, bias=1.0, max=1.0)")
anchors = [
    (0.0,     1),    # FLOOR: 1 LED per cap
    (1.5,     1),    # dimly-lit room: still on the FLOOR
    (5.0,     2),
    (10.0,    3),    # 3 LEDs per cap at PWM 1 starts here
    (50.0,    24),
    (100.0,   65),
    (200.0,   183),
    (250.0,   255),
    # Beyond saturation we just stay clamped at max.
    (1000.0,  255),
    (10000.0, 255),
]
for lux, want_B in anchors:
    check(f"lux={lux:<7g} -> B={want_B}", end_to_end(lux), want_B)


# --- Stage 1: monotone, smooth (no special low-lux regime) ---------------

print("\n# Curve is smooth across the whole lux range")
# Continuity is guaranteed by a single power formula. Spot-check
# the second derivative roughly: small lux steps produce small B
# steps, no jumps.
prev_B = end_to_end(0.0)
max_step = 0
for i in range(1, 250):
    B = end_to_end(float(i))
    step = B - prev_B
    if step > max_step:
        max_step = step
    prev_B = B
check("max step in B over lux 0..250 is small (<=4)",
      max_step <= 4, True)


# --- Stage 1: bias scales the lit_bonus, never the floor -----------------

print("\n# Bias scales lit_bonus only (floor at B=1 untouched)")
check("lux=0  bias=0.5 -> B=1", end_to_end(0.0,  bias=0.5), 1)
check("lux=0  bias=1.0 -> B=1", end_to_end(0.0,  bias=1.0), 1)
check("lux=0  bias=2.0 -> B=1", end_to_end(0.0,  bias=2.0), 1)
# At a lit-room lux, doubling bias should roughly double the lit_bonus.
b_lit_1 = end_to_end(50.0, bias=1.0)
b_lit_2 = end_to_end(50.0, bias=2.0)
check("lux=50 bias=2 brighter than bias=1",
      b_lit_2 > b_lit_1, True)


# --- Stage 1: saturation_lux scales the curve ---------------------------

print("\n# saturation_lux: curve reaches max at sat_lux (bias=1)")
# At lux == sat_lux, the curve hits the ceiling.
for sat in (50.0, 100.0, 250.0, 500.0, 1000.0):
    check(f"sat={sat:<6g}: lux=sat -> B=255",
          end_to_end(sat, bias=1.0, sat_lux=sat), 255)

# Above sat_lux the curve stays clamped at the ceiling.
check("sat=100: lux=200 == lux=10000 (both clamped)",
      end_to_end(200.0,  sat_lux=100.0)
      == end_to_end(10000.0, sat_lux=100.0), True)

# Lowering sat_lux makes the clock brighter at any lux below the new sat.
b_sat500 = end_to_end(50.0, sat_lux=500.0)
b_sat250 = end_to_end(50.0, sat_lux=250.0)
b_sat100 = end_to_end(50.0, sat_lux=100.0)
check("lux=50: lower sat_lux is brighter (500 < 250 < 100)",
      b_sat500 < b_sat250 < b_sat100, True)

# Floor is untouched by saturation_lux at lux=0.
check("sat=50:   lux=0 -> B=1", end_to_end(0.0, sat_lux=50.0), 1)
check("sat=2000: lux=0 -> B=1", end_to_end(0.0, sat_lux=2000.0), 1)


# --- Stage 1: brightness_bias is uniform make-up gain --------------------

print("\n# brightness_bias is uniform make-up gain")
# Doubling bias should double the lit_bonus at every lux below the
# max_b cap. Verify ratio of (target - FLOOR) doubles.
def lit_bonus_at(lux, bias):
    return auto_brightness_target(lux, bias=bias, max_b=10.0) - FLOOR

for lux in (5.0, 50.0, 100.0, 200.0):
    a = lit_bonus_at(lux, 1.0)
    b = lit_bonus_at(lux, 2.0)
    ratio = b / a if a > 0 else 0
    check(f"lux={lux:<5g}: bias 2 doubles lit_bonus (ratio {ratio:.3f})",
          approx(ratio, 2.0, 0.001), True)


# --- Stage 1: high bias accelerates saturation ---------------------------

print("\n# Bias>1 accelerates saturation")
# At bias=2, the curve should hit max well before saturation_lux.
sat_at_b1 = next(
    lux for lux in range(1, 1000) if end_to_end(float(lux), bias=1.0) >= 255
)
sat_at_b2 = next(
    lux for lux in range(1, 1000) if end_to_end(float(lux), bias=2.0) >= 255
)
check("bias=2 saturates earlier than bias=1",
      sat_at_b2 < sat_at_b1, True)


# --- Stage 1: max_brightness cap -----------------------------------------

print("\n# Max brightness cap")
# max_b=0.5 -> target capped at 0.5 -> B = round(127.5) = 128
check("lux=10000, max=0.5 -> B=128",
      end_to_end(10000.0, bias=1.0, max_b=0.5), 128)
# max_b=0.85 -> B = round(216.75) = 217
check("lux=10000, max=0.85 -> B=217",
      end_to_end(10000.0, bias=1.0, max_b=0.85), 217)
# Cap shouldn't affect dim conditions.
check("lux=1, max=0.5 -> B=1 (floor, not capped)",
      end_to_end(1.0, bias=1.0, max_b=0.5), 1)
# At a lux that would exceed the cap, it clips.
check("lux=200, max=0.5 -> B=128",
      end_to_end(200.0, bias=1.0, max_b=0.5), 128)


# --- Stage 1: monotonicity -----------------------------------------------

print("\n# Monotonic in lux (no decreasing steps)")
prev_B = -1
broken_at: float | None = None
for i in range(0, 10001):
    lux = i * 1.0
    B = end_to_end(lux)
    if B < prev_B:
        broken_at = lux
        break
    prev_B = B
check("monotone for lux in 0..10000", broken_at, None)


# --- Stage 2: brightness rounding ----------------------------------------

print("\n# user_brightness rounding edges")
check("0.0    -> B=0",   brightness_to_B(0.0),   0)
check("1/255  -> B=1",   brightness_to_B(1/255), 1)
check("0.5    -> B=128", brightness_to_B(0.5),   128)
check("1.0    -> B=255", brightness_to_B(1.0),   255)


# ---------------------------------------------------------------------------

print("\n----------------------------------------------------------------")
if FAILURES:
    print(f"FAILED ({len(FAILURES)} assertion(s)):")
    for f in FAILURES:
        print(f)
    sys.exit(1)
else:
    print("All brightness-mapping assertions passed.")
    sys.exit(0)
