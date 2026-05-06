#!/usr/bin/env python3
"""
Verify the brightness mapping pipeline used in clock.yaml.

The firmware does brightness in three stages:

  1. apply_auto_brightness script: smoothed lux is mapped through a
     three-region curve (dark band 1-2-3 + log-linear lit ramp) into a
     float target in 0..1 that is fed to the light's set_brightness().
  2. Display lambda: HA hands us back a float user_brightness in
     0..1; we round to an integer B in 0..255 and translate it to
     a max-channel level X with a small ladder at the dim end.
  3. paint_cap: turns FC.max = X into (n_leds_lit, per_led_pwm).

This script ports each stage to Python, then asserts the contracts
that define the curve:

  - Dark band (lux <= dim_lux) yields B in {1, 2, 3} only, regardless
    of brightness_offset. The napping-room floor is sacred.
  - Each of B=1, B=2, B=3 occupies a non-trivial sub-range of lux
    inside the dark band (so the 1/2/3 ladder actually resolves).
  - Continuity: at lux = dim_lux the lit ramp starts at B=3.
  - Lit ramp is linear in log(lux), reaching B=255 at lux = bright_lux.
  - brightness_offset multiplies the lit ramp output (in stops), but
    does NOT lift the dark band.
  - Max-brightness cap clamps the *target*, not B directly.
  - Curve is monotone in lux.
  - B in {1,2,3} drive the cap with 1, 2, 3 LEDs at PWM 1.
  - B >= 4 drives all 3 LEDs at PWM B-2.

Run as:  python3 tests/test_brightness_mapping.py
Exits non-zero on the first failed assertion.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Stage 1: apply_auto_brightness  (three-region curve)
# ---------------------------------------------------------------------------

FLOOR_B = 1.0 / 255.0   # B=1
B3      = 3.0 / 255.0
EPS     = 1e-3


def auto_brightness_target(
    lux: float,
    dim_lux: float = 5.0,
    bright_lux: float = 200.0,
    offset_stops: float = 0.0,
    max_b: float = 1.0,
) -> float:
    """Mirror of the apply_auto_brightness lambda in clock.yaml."""
    if lux < 0.0:
        lux = 0.0

    if dim_lux < 0.5:
        dim_lux = 0.5
    if bright_lux <= dim_lux:
        bright_lux = dim_lux + 1.0

    if lux <= dim_lux:
        # Dark band: log-quantise into B=1, 2, 3 across [dim/10, dim].
        night = dim_lux / 10.0
        lo = math.log(night + EPS)
        hi = math.log(dim_lux + EPS)
        t = (math.log(lux + EPS) - lo) / (hi - lo)
        if t < 0.0:
            t = 0.0
        if t > 1.0:
            t = 1.0
        step = int(t * 3.0)
        if step > 2:
            step = 2
        target = (1.0 + float(step)) / 255.0
    else:
        if lux >= bright_lux:
            t = 1.0
        else:
            t = (math.log(lux) - math.log(dim_lux)) / (
                math.log(bright_lux) - math.log(dim_lux)
            )
        target = B3 + t * (1.0 - B3)
        target *= 2.0 ** offset_stops
        if target < B3:
            target = B3

    if target > max_b:
        target = max_b
    return target


# ---------------------------------------------------------------------------
# Stage 2: user_brightness (0..1 float) -> B (0..255 int) -> X (max channel)
# ---------------------------------------------------------------------------

def brightness_to_B(user_brightness: float) -> int:
    """ESPHome stores brightness as a float; HA exposes it 0..255."""
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
    n_leds: int
    pwm: int


def paint_cap_white(X: float) -> CapOutput:
    LEDS_PER_CAP = 3
    max_c = X
    if max_c < 1.0 / 255.0:
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

    # round-then-cast: float32 in C lands at e.g. 0.99999988 for
    # X*scale, which a plain int()/(uint8_t) cast would truncate to 0.
    pwm = min(255, int(round(X * scale)))
    return CapOutput(n_leds=n, pwm=pwm)


def paint_cap_white_f32(X: float) -> CapOutput:
    """
    paint_cap mirror that uses float32 throughout, to catch the
    truncation-vs-rounding bug that bit us in the firmware. Without
    explicit rounding before the uint8_t cast, X * (1/X) lands at
    0.99999988 in float32 and the LED ends up at PWM 0 instead of 1.
    """
    import struct

    def f32(x: float) -> float:
        return struct.unpack("f", struct.pack("f", x))[0]

    LEDS_PER_CAP = 3
    max_c = f32(X)
    if max_c < f32(1.0 / 255.0):
        return CapOutput(0, 0)
    if max_c >= 1.0:
        n = LEDS_PER_CAP
        scale = f32(1.0)
    else:
        n = max(1, min(LEDS_PER_CAP, int(round(max_c * LEDS_PER_CAP))))
        scale = f32(1.0 / max_c)
    pwm = min(255, int(round(f32(max_c * scale))))
    return CapOutput(n_leds=n, pwm=pwm)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def end_to_end(
    lux: float,
    dim_lux: float = 5.0,
    bright_lux: float = 200.0,
    offset_stops: float = 0.0,
    max_b: float = 1.0,
) -> int:
    target = auto_brightness_target(lux, dim_lux, bright_lux,
                                    offset_stops, max_b)
    return brightness_to_B(target)


FAILURES: list[str] = []


def check(label: str, got, want):
    if got != want:
        FAILURES.append(f"  {label}: got {got!r}, want {want!r}")
        print(f"FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"ok    {label}: {got!r}")


# --- Stage 3: paint_cap ladder --------------------------------------------

print("\n# paint_cap ladder (white pixel)")
check("B=1  -> (1 LED, PWM 1)", paint_cap_white(B_to_X(1)),
      CapOutput(1, 1))
check("B=2  -> (2 LEDs, PWM 1)", paint_cap_white(B_to_X(2)),
      CapOutput(2, 1))
check("B=3  -> (3 LEDs, PWM 1)", paint_cap_white(B_to_X(3)),
      CapOutput(3, 1))
for B in (4, 5, 10, 50, 128, 255):
    check(f"B={B:<3d} -> (3 LEDs, PWM {B-2})",
          paint_cap_white(B_to_X(B)),
          CapOutput(3, B - 2))
check("B=0  -> all off", paint_cap_white(B_to_X(0)),
      CapOutput(0, 0))

# Dark-room floor is sacred: any non-trivially-zero colour going
# through paint_cap must light at least 1 LED at PWM 1. Mode/colour
# math at the dim end can pull max_c well below 1/3 -- if that
# blanked the cap, the auto-brightness floor (B=1) would be invisible
# in those modes.
print("\n# paint_cap dark-room floor (any non-zero colour -> >=1 LED)")
for X in (0.01, 0.05, 0.10, 0.15, 0.166, 0.20, 0.30):
    out = paint_cap_white(X)
    check(f"X={X:<5g} -> at least 1 LED at PWM 1",
          out.n_leds >= 1 and out.pwm >= 1, True)
# Truly-zero stays off (colon at fade=0, scale_fc(c, 0), etc).
check("X=0     -> all off", paint_cap_white(0.0), CapOutput(0, 0))
# Below 1/255 also rounds to off (no LED step can represent it).
check("X=0.001 -> all off", paint_cap_white(0.001), CapOutput(0, 0))

# Float32 regression: in C the per-channel PWM is computed as
# (uint8_t) fminf(255, c.r * scale). For B=1 (X=1/3) the float32
# product lands at 0.99999988 and a plain cast truncates to PWM 0,
# blanking the dark-room floor. Mirror that arithmetic here so a
# future tweak that drops the explicit round() in the firmware
# fails the test instead of the user.
print("\n# paint_cap float32 precision (mirrors C uint8_t cast)")
for B in (1, 2, 3):
    out = paint_cap_white_f32(B / 3.0)
    check(f"B={B}: float32 paint_cap_white -> >=1 LED at PWM 1",
          out.n_leds >= 1 and out.pwm >= 1, True)


# --- Dark band: only B=1/2/3, regardless of offset ------------------------

print("\n# Dark band: lux in [0, dim_lux] only ever yields B in {1,2,3}")
for offset in (-2.0, -1.0, 0.0, 1.0, 2.0):
    for lux in (0.0, 0.001, 0.1, 0.5, 1.0, 2.0, 4.99, 5.0):
        B = end_to_end(lux, offset_stops=offset)
        if B not in (1, 2, 3):
            check(f"lux={lux} offset={offset} -> B in {{1,2,3}}",
                  B, "1|2|3")
        else:
            print(f"ok    lux={lux:<6g} offset={offset:+.1f} -> B={B}")


# --- Dark band: each of B=1/2/3 occupies a non-trivial slice --------------

print("\n# Dark band: each B value occupies a non-trivial lux slice")
# Sample [0, dim_lux] log-uniformly (plus 0) and bin by B.
slices: dict[int, list[float]] = {1: [], 2: [], 3: []}
slices[end_to_end(0.0)].append(0.0)
n = 200
for i in range(1, n + 1):
    lux = 0.001 * (5.0 / 0.001) ** (i / n)   # log from 0.001 to 5
    B = end_to_end(lux)
    if B in slices:
        slices[B].append(lux)
for B, xs in slices.items():
    check(f"B={B} occupies >=10 lux samples in dark band",
          len(xs) >= 10, True)


# --- Continuity at dim_lux: lit ramp starts at B=3 ------------------------

print("\n# Continuity: at lux = dim_lux the lit ramp starts at B=3")
for dim in (2.0, 5.0, 10.0):
    check(f"lux=dim_lux={dim} -> B=3", end_to_end(dim, dim_lux=dim), 3)


# --- Lit ramp: hits B=255 at lux = bright_lux -----------------------------

print("\n# Lit ramp reaches B=255 at lux = bright_lux (offset=0)")
for bright in (50.0, 100.0, 200.0, 500.0, 1000.0):
    check(f"bright={bright}: lux=bright -> B=255",
          end_to_end(bright, bright_lux=bright), 255)
# Above bright_lux it stays clamped.
check("lux >> bright_lux is the same as lux=bright_lux",
      end_to_end(10000.0, bright_lux=200.0)
      == end_to_end(200.0, bright_lux=200.0), True)


# --- Lit ramp: linear in log(lux) ----------------------------------------

print("\n# Lit ramp is linear in log(lux): equal log steps -> equal B steps")
# Check three log-equidistant points have ~equal B deltas.
def b_at(lux):
    # use max_b=10.0 so the cap doesn't clip the test
    return auto_brightness_target(lux, max_b=10.0) * 255.0

# dim=5, bright=200 -> log ratio ~3.69; sample at 1/4, 2/4, 3/4 of range.
import math as _m
log_dim = _m.log(5.0)
log_bri = _m.log(200.0)
quarter_lux = [_m.exp(log_dim + (log_bri - log_dim) * q) for q in (0.25, 0.5, 0.75)]
b_vals = [b_at(x) for x in quarter_lux]
delta_a = b_vals[1] - b_vals[0]
delta_b = b_vals[2] - b_vals[1]
check("equal log-lux steps yield ~equal B steps (within 1)",
      abs(delta_a - delta_b) < 1.0, True)


# --- Brightness Offset: multiplies the lit ramp, leaves dark band alone ---

print("\n# Brightness Offset: multiplies lit ramp, dark band invariant")
# Above dim_lux: +1 stop ~ doubles the target (until cap)
for lux in (10.0, 30.0, 80.0):
    t0 = auto_brightness_target(lux, offset_stops=0.0, max_b=10.0)
    t1 = auto_brightness_target(lux, offset_stops=1.0, max_b=10.0)
    ratio = t1 / t0
    check(f"lux={lux}: +1 stop ~ doubles target (ratio={ratio:.3f})",
          abs(ratio - 2.0) < 0.05, True)

# Inside the dark band: target identical regardless of offset
for lux in (0.0, 0.5, 2.0, 4.99):
    t_neg = auto_brightness_target(lux, offset_stops=-2.0)
    t_pos = auto_brightness_target(lux, offset_stops=+2.0)
    check(f"lux={lux} dark-band invariant to offset",
          t_neg == t_pos, True)


# --- Lit ramp lower bound: offset never drags lit ramp below B=3 ---------

print("\n# Lit ramp never drops below B=3, even with -2 stops")
for lux in (5.001, 10.0, 50.0, 199.0):
    B = end_to_end(lux, offset_stops=-2.0)
    check(f"lux={lux} offset=-2 -> B>=3", B >= 3, True)


# --- Max brightness cap ---------------------------------------------------

print("\n# Max brightness cap")
check("lux=10000, max=0.5 -> B=128",
      end_to_end(10000.0, max_b=0.5), 128)
check("lux=10000, max=0.85 -> B=217",
      end_to_end(10000.0, max_b=0.85), 217)
check("lux=0.5, max=0.5 -> dark band still works (B in {1,2,3})",
      end_to_end(0.5, max_b=0.5) in (1, 2, 3), True)


# --- Monotonicity ---------------------------------------------------------

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
