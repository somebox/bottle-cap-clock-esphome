#!/usr/bin/env python3
"""
Generate an SVG plot of the three-region auto-brightness curve.

Two panels, both on a log-x lux axis:
  Left:  Dim Lux / Bright Lux variations at offset = 0.
         Shows how moving the two anchors widens or shifts the curve.
  Right: Brightness Offset variations at default anchors.
         Shows that the dark band (lux <= dim_lux) is invariant to the
         offset, while the lit ramp shifts in stops.

The curve math mirrors apply_auto_brightness in clock.yaml. If you
change the curve in the YAML, re-run this script to refresh the
plot:

    python3 tools/plot_brightness_curve.py
"""

from __future__ import annotations

import math
from pathlib import Path


# ---------------------------------------------------------------------------
# Curve (mirrors clock.yaml's apply_auto_brightness lambda)
# ---------------------------------------------------------------------------

FLOOR_B = 1.0 / 255.0
B3      = 3.0 / 255.0
EPS     = 1e-3


def target(lux: float, dim_lux: float = 5.0, bright_lux: float = 200.0,
           offset_stops: float = 0.0, max_b: float = 1.0) -> float:
    if lux < 0.0:
        lux = 0.0
    if dim_lux < 0.5:
        dim_lux = 0.5
    if bright_lux <= dim_lux:
        bright_lux = dim_lux + 1.0

    if lux <= dim_lux:
        night = dim_lux / 10.0
        lo = math.log(night + EPS)
        hi = math.log(dim_lux + EPS)
        t = (math.log(lux + EPS) - lo) / (hi - lo)
        t = max(0.0, min(1.0, t))
        step = min(2, int(t * 3.0))
        return min(max_b, (1.0 + step) / 255.0)

    if lux >= bright_lux:
        t = 1.0
    else:
        t = (math.log(lux) - math.log(dim_lux)) / (
            math.log(bright_lux) - math.log(dim_lux)
        )
    out = B3 + t * (1.0 - B3)
    out *= 2.0 ** offset_stops
    if out < B3:
        out = B3
    return min(max_b, out)


def to_brightness_pct(t: float) -> float:
    return t * 100.0


# ---------------------------------------------------------------------------
# SVG plotting helpers (no external deps)
# ---------------------------------------------------------------------------

PANEL_W = 460
PANEL_H = 300
MARGIN = {"l": 56, "r": 24, "t": 44, "b": 56}
GAP = 50

PLOT_W = PANEL_W - MARGIN["l"] - MARGIN["r"]
PLOT_H = PANEL_H - MARGIN["t"] - MARGIN["b"]

LUX_MIN = 0.1
LUX_MAX = 1500.0
B_MAX = 100.0

PALETTE_SEQ = ["#08306b", "#2171b5", "#41ab5d", "#fb6a4a", "#cb181d"]


def x(lux: float, panel_x_offset: int) -> float:
    """Log-scale x axis: log(LUX_MIN) maps to plot left, log(LUX_MAX) to right."""
    if lux < LUX_MIN:
        lux = LUX_MIN
    t = (math.log(lux) - math.log(LUX_MIN)) / (math.log(LUX_MAX) - math.log(LUX_MIN))
    return panel_x_offset + MARGIN["l"] + t * PLOT_W


def y(b_pct: float) -> float:
    return MARGIN["t"] + (1 - b_pct / B_MAX) * PLOT_H


# Decade ticks: 0.1, 1, 10, 100, 1000
def axis_x_ticks() -> list[tuple[float, str]]:
    return [(0.1, "0.1"), (1, "1"), (10, "10"), (100, "100"), (1000, "1000")]


def axis_y_ticks() -> list[float]:
    return [0, 25, 50, 75, 100]


def panel(panel_x_offset: int, title: str, curves: list[dict],
          palette: list[str]) -> str:
    parts: list[str] = []

    parts.append(
        f'<rect x="{panel_x_offset + MARGIN["l"]}" y="{MARGIN["t"]}" '
        f'width="{PLOT_W}" height="{PLOT_H}" fill="#fafafa" '
        f'stroke="#ddd" stroke-width="1" />'
    )

    for tx_lux, label in axis_x_ticks():
        sx = x(tx_lux, panel_x_offset)
        parts.append(
            f'<line x1="{sx}" y1="{MARGIN["t"]}" '
            f'x2="{sx}" y2="{MARGIN["t"] + PLOT_H}" '
            f'stroke="#eee" stroke-width="1" />'
        )
        parts.append(
            f'<text x="{sx}" y="{MARGIN["t"] + PLOT_H + 18}" '
            f'font-size="11" font-family="sans-serif" text-anchor="middle" '
            f'fill="#555">{label}</text>'
        )

    for ty in axis_y_ticks():
        sy = y(ty)
        parts.append(
            f'<line x1="{panel_x_offset + MARGIN["l"]}" y1="{sy}" '
            f'x2="{panel_x_offset + MARGIN["l"] + PLOT_W}" y2="{sy}" '
            f'stroke="#eee" stroke-width="1" />'
        )
        parts.append(
            f'<text x="{panel_x_offset + MARGIN["l"] - 8}" y="{sy + 4}" '
            f'font-size="11" font-family="sans-serif" text-anchor="end" '
            f'fill="#555">{int(ty)}%</text>'
        )

    parts.append(
        f'<text x="{panel_x_offset + MARGIN["l"] + PLOT_W / 2}" '
        f'y="{MARGIN["t"] - 16}" font-size="13" font-family="sans-serif" '
        f'font-weight="600" text-anchor="middle" fill="#222">{title}</text>'
    )
    parts.append(
        f'<text x="{panel_x_offset + MARGIN["l"] + PLOT_W / 2}" '
        f'y="{MARGIN["t"] + PLOT_H + 40}" font-size="11" '
        f'font-family="sans-serif" text-anchor="middle" fill="#444">'
        f'Ambient lux (log scale)</text>'
    )
    parts.append(
        f'<text transform="rotate(-90 {panel_x_offset + 14} '
        f'{MARGIN["t"] + PLOT_H / 2})" x="{panel_x_offset + 14}" '
        f'y="{MARGIN["t"] + PLOT_H / 2}" font-size="11" '
        f'font-family="sans-serif" text-anchor="middle" fill="#444">'
        f'Brightness (% of 255)</text>'
    )

    legend_x = panel_x_offset + MARGIN["l"] + 8
    legend_y = MARGIN["t"] + 10

    # Sample log-uniformly across LUX_MIN..LUX_MAX so the staircase in
    # the dark band shows up cleanly without aliasing.
    n_samples = 1200
    lux_samples = [
        LUX_MIN * (LUX_MAX / LUX_MIN) ** (i / (n_samples - 1))
        for i in range(n_samples)
    ]

    for i, curve in enumerate(curves):
        color = palette[i % len(palette)]
        dim_l  = curve.get("dim_lux",    5.0)
        brt_l  = curve.get("bright_lux", 200.0)
        offset = curve.get("offset",     0.0)

        pts = [
            (x(lx, panel_x_offset),
             y(to_brightness_pct(target(lx, dim_l, brt_l, offset))))
            for lx in lux_samples
        ]
        d = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" '
            f'stroke-width="2" />'
        )

        ly = legend_y + i * 18
        parts.append(
            f'<line x1="{legend_x}" y1="{ly}" x2="{legend_x + 24}" y2="{ly}" '
            f'stroke="{color}" stroke-width="2" />'
        )
        parts.append(
            f'<text x="{legend_x + 30}" y="{ly + 4}" font-size="11" '
            f'font-family="sans-serif" fill="#222">{curve["label"]}</text>'
        )

    return "\n".join(parts)


def generate_svg() -> str:
    total_w = PANEL_W * 2 + GAP
    total_h = PANEL_H + 30

    # Left: vary the two lux anchors, offset = 0.
    left_curves = [
        {"label": "dim 2  / bright 100",            "dim_lux": 2.0,  "bright_lux": 100.0},
        {"label": "dim 5  / bright 200 (default)",  "dim_lux": 5.0,  "bright_lux": 200.0},
        {"label": "dim 5  / bright 300",            "dim_lux": 5.0,  "bright_lux": 300.0},
        {"label": "dim 10 / bright 500",            "dim_lux": 10.0, "bright_lux": 500.0},
        {"label": "dim 20 / bright 1000",           "dim_lux": 20.0, "bright_lux": 1000.0},
    ]
    # Right: vary brightness offset at default anchors.
    right_curves = [
        {"label": "offset -1 EV", "offset": -1.0},
        {"label": "offset -0.5",  "offset": -0.5},
        {"label": "offset 0 (default)", "offset": 0.0},
        {"label": "offset +0.5",  "offset": +0.5},
        {"label": "offset +1 EV", "offset": +1.0},
    ]

    body = []
    body.append(panel(0, "Dim Lux / Bright Lux (offset = 0)",
                      left_curves, PALETTE_SEQ))
    body.append(panel(PANEL_W + GAP, "Brightness Offset (dim=5, bright=200)",
                      right_curves, PALETTE_SEQ))

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" '
        f'width="{total_w}" height="{total_h}" '
        f'font-family="sans-serif">\n'
        f'<rect width="100%" height="100%" fill="white" />\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "doc" / "auto-brightness-curve.svg"
    out.write_text(generate_svg())
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
