#!/bin/bash

# Navigate to the project directory regardless of where double-click launches from
cd "$(dirname "$0")"

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing dependencies..."
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# Copy .env.example to .env if .env doesn't exist yet
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  First run: add your Mapbox API key to .env before continuing."
    echo "    File is at: $(pwd)/.env"
    echo ""
    open .env
    read -p "Press Enter once you've saved your API key..."
fi

TK_SILENCE_DEPRECATION=1 python3 main.py
