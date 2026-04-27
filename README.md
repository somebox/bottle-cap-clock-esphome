# Bottle Cap Clock

A digital clock made from RGB pixels with recycled PET bottle caps to make the clock face.
Works with ESPHome on a custom ESP32-S3 board.

Each digit is a 5 row × 3 column grid (15 LEDs), with the LED strip laid out in a serpentine pattern:

```txt
Col:  0   1   2
    +---+---+---+
R0  | 0 | 9 |10 |
R1  | 1 | 8 |11 |
R2  | 2 | 7 |12 |
R3  | 3 | 6 |13 |
R4  | 4 | 5 |14 |
    +---+---+---+
```

Column 0 runs top-to-bottom (0→4), column 1 runs bottom-to-top (5→9), column 2 runs top-to-bottom (10→14).

**LED Strip Layout (65 LEDs total):**
| Segment | LEDs | Count |
|---------|------|-------|
| Digit 0 (hours tens) | 0-14 | 15 |
| Digit 1 (hours units) | 15-29 | 15 |
| Colon | 30-34 | 5 |
| Digit 2 (minutes tens) | 35-49 | 15 |
| Digit 3 (minutes units) | 50-64 | 15 |

The colon uses LEDs at indices 1 and 3 (2nd and 4th pixel) which blink every second.

## Hardware

Custom ESP32-S3 board with pinout based on Espressif S3 devkit and Adafruit Feather S3.

| Pin | Function |
|-----|----------|
| IO2 | Heartbeat LED |
| IO6 | User Button |
| IO8 | I2C SDA |
| IO9 | I2C SCL |
| IO11 | Addressable LEDs (clock segments) |
| IO16 | Addressable LEDs (alternate) |
| IO12, 13, 21 | Rotary encoder (optional) |
| IO17, 18 | UART1 |
| IO35-37 | SPI |

**Sensors:**
- BH1750 ambient light sensor (I2C, address 0x23)

## Setup

1. Clone this repository
2. Run the setup script (or manually create a venv and `pip install -r requirements.txt`):

   ```bash
   ./setup.sh
   source venv/bin/activate
   ```

3. Copy `secrets.yaml.example` to `secrets.yaml` and add your WiFi credentials:

   ```yaml
   wifi_ssid: "YourNetworkName"
   wifi_password: "YourPassword"
   ```

4. Connect the ESP32-S3 board via USB-C

5. Flash the device:

   ```bash
   source venv/bin/activate
   esphome run clock.yaml
   ```

After the first flash, OTA updates are available over WiFi.

## Project Structure

- `clock.yaml` - Main ESPHome configuration
- `secrets.yaml` - WiFi credentials (not tracked in git)
- `secrets.yaml.example` - Template for secrets
- `requirements.txt` - Python dependencies
- `setup.sh` - Setup script to create venv and install dependencies

