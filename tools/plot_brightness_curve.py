#!/usr/bin/env python3
"""
Generate an SVG plot of the auto-brightness curve.

Two panels:
  Left:  Brightness Bias as make-up gain (sat_lux fixed at 250).
         Several bias values overlaid on the same axes.
  Right: Saturation Lux as the curve's reference / saturation
         point (bias = 1.0). Lower sat compresses the lux range
         so the clock saturates earlier; higher sat stretches it.

The curve math mirrors apply_auto_brightness in clock.yaml. If you
change the curve in the YAML, re-run this script to refresh the
plot:

    python3 tools/plot_brightness_curve.py
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Curve (mirrors clock.yaml's apply_auto_brightness lambda)
# ---------------------------------------------------------------------------

FLOOR = 1.0 / 255.0
CURVE_P = 1.5


def target(lux: float, bias: float = 1.0, sat_lux: float = 250.0,
           max_b: float = 1.0) -> float:
    if lux < 0.0:
        lux = 0.0
    sat = max(sat_lux, 1.0)
    ratio = min(lux / sat, 1.0)
    lit_bonus = (ratio ** CURVE_P) * (1.0 - FLOOR)
    t = FLOOR + lit_bonus * bias
    return min(t, max_b)


def to_brightness_pct(t: float) -> float:
    return t * 100.0


# ---------------------------------------------------------------------------
# SVG plotting helpers (no external deps)
# ---------------------------------------------------------------------------

PANEL_W = 420
PANEL_H = 280
MARGIN = {"l": 50, "r": 20, "t": 40, "b": 50}
GAP = 50

PLOT_W = PANEL_W - MARGIN["l"] - MARGIN["r"]
PLOT_H = PANEL_H - MARGIN["t"] - MARGIN["b"]

LUX_MAX = 500.0
B_MAX = 100.0

# Categorical palette for the bias panel (distinct hues).
PALETTE_CAT = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728", "#000000"]

# Sequential palette for the sat panel (curves have a natural
# ordering by sat_lux). Goes from dim/cool to bright/warm so the
# eye can read "smaller sat" -> "leftmost / coolest curve".
PALETTE_SEQ = ["#08306b", "#2171b5", "#41ab5d", "#fb6a4a", "#cb181d"]


def x(lux: float, panel_x_offset: int) -> float:
    return panel_x_offset + MARGIN["l"] + (lux / LUX_MAX) * PLOT_W


def y(b_pct: float) -> float:
    return MARGIN["t"] + (1 - b_pct / B_MAX) * PLOT_H


def axis_x_ticks() -> list[float]:
    return [0, 100, 200, 300, 400, 500]


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

    for tx in axis_x_ticks():
        sx = x(tx, panel_x_offset)
        parts.append(
            f'<line x1="{sx}" y1="{MARGIN["t"]}" '
            f'x2="{sx}" y2="{MARGIN["t"] + PLOT_H}" '
            f'stroke="#eee" stroke-width="1" />'
        )
        parts.append(
            f'<text x="{sx}" y="{MARGIN["t"] + PLOT_H + 18}" '
            f'font-size="11" font-family="sans-serif" text-anchor="middle" '
            f'fill="#555">{int(tx)}</text>'
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
        f'y="{MARGIN["t"] - 14}" font-size="13" font-family="sans-serif" '
        f'font-weight="600" text-anchor="middle" fill="#222">{title}</text>'
    )
    parts.append(
        f'<text x="{panel_x_offset + MARGIN["l"] + PLOT_W / 2}" '
        f'y="{MARGIN["t"] + PLOT_H + 38}" font-size="11" '
        f'font-family="sans-serif" text-anchor="middle" fill="#444">'
        f'Ambient lux</text>'
    )
    parts.append(
        f'<text transform="rotate(-90 {panel_x_offset + 14} '
        f'{MARGIN["t"] + PLOT_H / 2})" x="{panel_x_offset + 14}" '
        f'y="{MARGIN["t"] + PLOT_H / 2}" font-size="11" '
        f'font-family="sans-serif" text-anchor="middle" fill="#444">'
        f'Brightness (% of 255)</text>'
    )

    legend_x = panel_x_offset + MARGIN["l"] + PLOT_W - 130
    legend_y = MARGIN["t"] + 10

    for i, curve in enumerate(curves):
        color = palette[i % len(palette)]
        bias = curve.get("bias", 1.0)
        sat = curve.get("sat", 250.0)
        lux_samples = [j * (LUX_MAX / 500.0) for j in range(501)]
        pts = [
            (x(lx, panel_x_offset), y(to_brightness_pct(target(lx, bias, sat))))
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

    left_curves = [
        {"label": "bias 0.5",           "bias": 0.5, "sat": 250.0},
        {"label": "bias 1.0 (default)", "bias": 1.0, "sat": 250.0},
        {"label": "bias 1.5",           "bias": 1.5, "sat": 250.0},
        {"label": "bias 2.0",           "bias": 2.0, "sat": 250.0},
        {"label": "bias 3.0",           "bias": 3.0, "sat": 250.0},
    ]
    right_curves = [
        {"label": "sat 100", "bias": 1.0, "sat": 100.0},
        {"label": "sat 200", "bias": 1.0, "sat": 200.0},
        {"label": "sat 300", "bias": 1.0, "sat": 300.0},
        {"label": "sat 500", "bias": 1.0, "sat": 500.0},
        {"label": "sat 1000","bias": 1.0, "sat": 1000.0},
    ]

    body = []
    body.append(panel(0, "Brightness Bias (sat_lux = 250)",
                      left_curves, PALETTE_CAT))
    body.append(panel(PANEL_W + GAP, "Saturation Lux (bias = 1.0)",
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
