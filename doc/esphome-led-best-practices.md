# ESPHome + ESP32 + addressable LEDs: practical notes

Lessons distilled from building the Bottle Cap Clock. Aimed at small/medium
addressable-LED projects on the ESP32 family (especially S3) running
ESPHome, but most of the persistence and structural advice applies to any
ESPHome device.

## Driver choice for WS2812-class LEDs

On ESP32-S3 the `neopixelbus` light platform with `method: esp32_rmt` uses
the **legacy** ESP-IDF RMT driver. It produces unreliable timing past a few
dozen LEDs, with garbage colors that change between reboots. Symptoms:

- Pixels lighting up that shouldn't be
- Dim green tints inside otherwise-correct clusters
- Mapping that "drifts" further along the strip
- Inconsistent behaviour between reboots

Use the native ESPHome platform instead:

```yaml
light:
  - platform: esp32_rmt_led_strip
    chipset: WS2812
    rgb_order: GRB
    pin: GPIOxx
    num_leds: <count>
```

This uses the new ESP-IDF RMT TX driver, which is the supported path on the
S3 and matches what FastLED uses internally.

### RMT / DMA tuning

For strips longer than ~30 LEDs on S3, enable DMA and pick safe values:

```yaml
    use_dma: true
    rmt_symbols: 1024     # max accepted with DMA on S3; 4800 fails
    use_psram: false      # keep framebuffer in SRAM for deterministic timing
```

Without `use_dma: true`, Wi-Fi or scheduler interrupts can cause RMT buffer
underruns mid-frame. The default `rmt_symbols: 64` makes that more likely
because each DMA descriptor only covers ~3 LEDs.

`use_psram: false` matters because the strip framebuffer is read out at LED
clock rate; PSRAM cache misses can stretch reads past the WS2812 timing
budget and produce flicker.

### Power supply expectations

WS2812 strips draw up to ~60 mA per LED at full white. USB power is for
**flashing only** — design for the deployed PSU and don't try to fit your
firmware around USB current limits. If you really need lower current
during USB programming, dim the strip via an `on_boot` action rather than
hard-capping `max_brightness` (which would also cap the deployed unit).

## Pixel mapping

For non-trivial physical layouts (matrices, serpentine wiring, multi-LED
"super-pixels"), do this:

1. **Lookup table**, not arithmetic. Computing serpentine offsets inline is
   error-prone and hard to debug. A small `static const uint8_t
   ROW_COL_TO_CAP[ROWS][COLS]` table inside the lambda is fast and obvious.

2. **Logical vs physical coordinates**. If you support rotation/mirroring
   modes, keep the input coordinates "what the user sees" and translate to
   physical strip indices in the rendering step. That way effects that work
   in screen space (rainbows, waves) stay correct after rotation.

3. **Group helpers**. If each logical pixel is N physical LEDs, define a
   `paint_cap(int cap, Color c)` lambda once and use it everywhere.

4. **Ship diagnostic effects** alongside the real one and keep them in the
   firmware. Two are enough to debug almost any layout problem:
   - "Walk": light one logical pixel at a time, log the index.
   - "Test pattern": render fixed known content (digit/glyph/grid).

The Bottle Cap Clock keeps both as named effects on the light, so you can
switch to them from the web UI without re-flashing.

## Avoiding display flicker

Things that caused visible flicker in our project and the fixes:

- **`default_transition_length`** defaults to 1000 ms on lights. For a
  pixel-mapped display every per-frame state push triggers a transition.
  Set `default_transition_length: 0s`.
- **`gamma_correct`** defaults to 2.8. Useful for monochromatic dimmable
  bulbs, harmful for crisp pixel art. Set to `1.0`.
- **Time/state desyncs**: if your render lambda blanks the frame whenever
  some upstream state is "not ready" (e.g. SNTP not yet valid), brief
  desyncs cause flashes. Cache the last valid value across frames and only
  blank on hard "never had data" boot state.
- **Diagnostics that briefly clear individual LEDs**: clear the whole
  framebuffer at the top of the lambda, not piecemeal.

## Sensor smoothing without breaking responsiveness

ESPHome's sensor pipeline runs filters between the raw read and the
publish. `on_value` fires *after* filters; `on_raw_value` fires before.

If you smooth a sensor heavily (median, throttle, sliding window) and use
the smoothed value to drive a UI element, fast inputs (slider drags,
toggles) will appear unresponsive — the response is gated by the throttle.

Pattern that keeps both calm history and responsive controls:

```yaml
sensor:
  - platform: <something>
    update_interval: 10s
    filters:
      - median: { window_size: 6, send_every: 6, send_first_at: 1 }
      - throttle: 30s
    on_raw_value:
      then:
        - lambda: 'id(last_reading) = x;'
        - script.execute: apply_setting

script:
  - id: apply_setting
    mode: restart
    then:
      - lambda: |-
          float v = id(last_reading);
          // ... derive control output from v + other state ...
```

The published entity stays calm (60 s smoothing, 30 s minimum gap), the
control reacts on every raw 10 s sample, and any UI control that should
"apply now" calls `script.execute: apply_setting` directly without
forcing a sensor read.

`mode: restart` on the script collapses rapid repeated triggers (e.g. a
slider drag) to a single execution.

## Persistence: entity-owned, not global-owned

ESPHome stores persisted values in flash preferences keyed by a hash. There
are two storage scopes that matter:

- **Entity persistence**: `restore_mode` on switches/lights, `restore_value`
  on selects/numbers/templates. Keyed by the entity ID. Stable across
  firmware structure changes.
- **Global persistence**: `restore_value: true` on a `globals:` entry.
  Keyed by a hash that's more sensitive to firmware structure.

Symptom we hit: a `Rotate 180` template switch with a `lambda:` that read
from a global with `restore_value: true` would lose its state on every
flash, while a sibling `Clock Mode` `select` with `restore_value: true` on
the entity itself persisted reliably.

The lesson is to **make the entity the source of truth**:

```yaml
switch:
  - platform: template
    name: "My Toggle"
    id: my_toggle
    optimistic: true                   # entity owns the state
    restore_mode: RESTORE_DEFAULT_OFF  # persisted, replayed on boot
    turn_on_action:
      - globals.set: { id: my_flag, value: 'true' }
    turn_off_action:
      - globals.set: { id: my_flag, value: 'false' }

globals:
  - id: my_flag        # runtime cache, NOT the persistence point
    type: bool
    restore_value: false
    initial_value: 'false'
```

`RESTORE_DEFAULT_OFF` replays `turn_off_action` on boot, which re-syncs
the global. `optimistic: true` makes the switch trust its own stored state
rather than recomputing from a lambda.

If a render lambda needs to read the value many times per frame, mirror it
to a global (as above) — calling `id(my_toggle).state` across an entity
boundary is fine but more expensive.

## "Made for ESPHome" / sharing-friendly configuration

If anyone other than you is going to flash your YAML, follow the rules in
[Made for ESPHome](https://esphome.io/guides/made_for_esphome/) and
[Sharing](https://esphome.io/guides/creators/). Concretely:

- **No `!secret` references** in the shared YAML.
- Use `wifi: ap:` + `captive_portal:` + `improv_serial:` + `esp32_improv:`
  for first-time provisioning.
- `esphome:` block: set `name`, `friendly_name`, `name_add_mac_suffix: true`,
  and a `project:` metadata block.
- `dashboard_import:` with a `package_import_url` pointing at the raw YAML
  in your fork enables one-click adoption from the ESPHome dashboard.
- Every entity gets an explicit `id:` so users can reference / extend / remove
  it without renaming everything.
- Lights with user-customisable colors should set
  `restore_mode: RESTORE_DEFAULT_ON` so chosen color and brightness survive
  reboots.

For local development, replace the `wifi: ap:` block with `!secret`
references temporarily; don't commit that change.

## YAML structure

A predictable section order makes large configs much easier to navigate:

1. `substitutions:` (name, friendly_name, version, pins, timezone, etc.)
2. `esphome:` (with `project:` and `on_boot:`)
3. `esp32:` / platform block
4. `psram:` if used
5. `logger:`, `api:`, `ota:`
6. `wifi:`, `captive_portal:`, `improv_serial:`, `esp32_improv:`
7. `dashboard_import:`
8. `web_server:`
9. Buses: `i2c:`, `spi:`, `uart:`
10. `time:`
11. Hardware components: `output:`, `binary_sensor:`, `sensor:`
12. State: `globals:`, `script:`
13. UI controls: `switch:`, `select:`, `number:`, `button:`
14. Output components last: `light:`, `display:`

Substitute every GPIO pin so users with slightly different wiring can
override without diffing the rest of the file:

```yaml
substitutions:
  led_pin: GPIO16
  i2c_sda_pin: GPIO8
# ...
light:
  - pin: ${led_pin}
i2c:
  sda: ${i2c_sda_pin}
```

## Lambda guidance

Inside `addressable_lambda` and similar:

- **Static const tables** are compiled once and live in flash; perfect for
  digit bitmaps, lookup tables, palettes.
- **Static cache variables** (`static int s_hour = 0;`) hold state between
  frames without needing globals.
- **Use small helper lambdas** (`paint_cap`, `hsv_to_color`, `scale_color`)
  to keep the main loop short.
- **Avoid string compares per frame**. If a `select:` drives a render
  branch, mirror it to an `int` global in the select's `on_value`, then
  the render lambda can `switch (id(mode))`.

## Brightness mapping for ambient-light-driven displays

Driving a pixel display from a lux sensor looks easy ("just map lux to
brightness") and isn't. The Bottle Cap Clock has 65 caps × 3 LEDs each on
WS2812s, with a uint8 PWM per channel. Keeping it readable across rooms
that span ~0 lux (bedroom at night) to ~10000 lux (sunlight) without
flicker, hunting, or unreadable extremes took several iterations. The
useful lessons:

### Separate the concerns: curve scale, gain, hard cap

It is tempting to fold lux response, user gain, and a "max" cap into one
formula. Don't. Each does a different thing and benefits from being a
separate, named control:

1. **Curve scale** (`saturation_lux`): the lux value at which the curve
   reaches maximum brightness. Sets the *horizontal* scale — what the
   sensor's "loud" looks like to the display. Lower values compress the
   response into a dim room; higher values stretch it for sunlit rooms.
2. **Make-up gain** (`bias`): a uniform multiplier on the lit-room bonus.
   Sets the *vertical* scale without moving the saturation point.
3. **Hard cap** (`max_brightness`): a ceiling on the final value. Useful
   for protecting against thermal/PSU limits or muting the display.

We iterated through two designs before settling here. An earlier attempt
treated `sat_lux` as a hard input clamp (`min(lux, sat_lux)`) and used a
fixed `REFERENCE_LUX` for the curve shape. That gave clean orthogonality
("below sat_lux, output is independent of sat_lux") but meant the user
couldn't actually calibrate where the display saturated — they could only
veto values above some lux. The current design uses `sat_lux` as the
curve's own reference so that lowering it actually pulls saturation
inward, which matches what users mean by "max brightness in *my* room is
about 200 lux".

```cpp
// Pseudocode
float ratio     = min(lux / sat_lux, 1.0f);          // saturates at sat_lux
float lit_bonus = powf(ratio, CURVE_P) * (1.0f - FLOOR);
float target    = FLOOR + lit_bonus * gain;          // make-up gain
target          = min(target, max_b);                // hard cap
```

The trade-off worth being explicit about: `sat_lux` is *not* orthogonal to
mid-range brightness. Halving `sat_lux` doubles the brightness at every
lux below the new saturation point. That's fine if the controls are
labelled honestly — `sat_lux` is the room-calibration knob, `bias` is the
"make it dimmer/brighter without recalibrating" knob.

### Floor must be bias-invariant

If you let the user gain multiply the whole formula, a low-gain setting
can mathematically push the dark-room output below the dimmest LED step,
and a high-gain setting can lift the floor above the dim level you
designed. Both are surprising. Structure the formula so the floor is an
*addition*, not a multiplication:

```cpp
target = FLOOR + (lit_bonus * gain);   // floor is additive, not scaled
```

Then a pitch-dark room always lands at `FLOOR` regardless of gain, and
testing this is a one-liner.

### Sub-pixel dimming for very low brightness

WS2812s produce visible output at PWM 1 but nothing visible at PWM 0. If
your "logical pixel" is N physical LEDs, you can extend the dim end by
lighting fewer LEDs at PWM 1 instead of dimming all of them below
visibility. For 3 LEDs per pixel:

| HA brightness `B` | Per-pixel rendering |
|---|---|
| 0 | Off |
| 1 | 1 LED at PWM 1 |
| 2 | 2 LEDs at PWM 1 |
| 3 | 3 LEDs at PWM 1 |
| ≥4 | 3 LEDs at PWM `B - 2` |

This adds three usable sub-steps below "all 3 LEDs at PWM 1", which
makes the difference between a dark-room display being "visible but
quiet" vs "off or glaring".

Implement it inside the cap-rendering helper (`paint_cap` in our case) so
both manual control (HA slider) and automatic control benefit, and so the
ladder transition is invisible from the rest of the rendering code.

### Test the math separately from the firmware

Lux mapping pipelines have many continuous parameters and a few hard
edges (floor, max cap, limiter threshold). They are perfect targets for a
small Python script that mirrors the C++ math and asserts properties like:

- Pitch-dark room → brightest sub-pixel state, regardless of gain
- Curve reaches max brightness exactly at `sat_lux` (with gain = 1.0)
- Doubling gain → doubles `lit_bonus` at every lux level
- Curve is monotonic across the full lux range
- Float→`B` rounding edges (`0.0`, `1/255`, `0.5`, `1.0`) land on the
  expected integers

A handful of asserts catches every shape regression we introduced while
iterating, without needing to flash a board.

A standalone script that re-renders the curve to SVG (no plotting library,
just pure stdlib) makes the docs easy to keep up to date — re-running it
after a constant change refreshes the diagram in one step.

## Effect colour math

A small set of helpers covers most modes:

- `hsv_to_color(h, s, v)`: rainbow-style spatial gradients.
- `scale_color(c, k)`: brightness-preserving fades / wave amplitudes.
- **Channel rotation**: `Color(c.g, c.b, c.r)` (or `c.b, c.r, c.g`) is a
  cheap, visually-distinct secondary colour that follows the user's
  primary choice. Useful for bicolor / dual-mode displays.

When the light entity has `gamma_correct: 1.0`, scaling by a float in
`[0, 1]` is perceptually OK for fades on small/many-LED displays. If you
need crisper low-end fades, raise `k` to a power (`powf(k, 0.45f)`) before
multiplying.

## Debug strategy when things look wrong

1. **Trust the hardware until proven otherwise** if it works with a
   different firmware. Don't blame wiring without evidence.
2. **Compare against a known-good reference**. A 30-line Arduino + FastLED
   sketch that does a basic chase is invaluable for isolating
   driver/library issues from logic issues.
3. **Move down the stack only when needed**: pixel math → mapping table →
   library/driver → board/wiring/PSU. The driver layer is the one that
   surprises people most on the S3.
4. **Keep diagnostic effects in the firmware** so you can switch into them
   from the web UI without re-flashing.
