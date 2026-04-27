#!/bin/bash
# Setup script for bottle-cap-clock-esphome

set -e

echo "Creating Python virtual environment..."
python3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "Setup complete!"
echo ""
echo "To activate the environment, run:"
echo "  source venv/bin/activate"
echo ""
echo "Then copy secrets.yaml.example to secrets.yaml and add your WiFi credentials."
