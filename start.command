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

# No API keys needed — terrain (AWS) and imagery (ESRI) are both keyless.

TK_SILENCE_DEPRECATION=1 python3 main.py
status=$?

# On success, auto-close this Terminal window. On error, leave it open so the
# message stays readable. Targets only the window running this script (by tty).
if [ "$status" -eq 0 ]; then
    my_tty="$(tty)"
    /usr/bin/osascript >/dev/null 2>&1 <<OSA &
tell application "Terminal"
    repeat with w in windows
        try
            if (tty of selected tab of w) is "$my_tty" then
                close w saving no
                exit repeat
            end if
        end try
    end repeat
end tell
OSA
fi
