#!/bin/bash

TITLE="$1"
X=${2:-100}
Y=${3:--300}

if [ -z "$TITLE" ]; then
  echo "Usage: $0 <partial window title> [x] [y]"
  exit 1
fi

WIN_ID=$(xdotool search --name "$TITLE" | tail -n1)

if [ -z "$WIN_ID" ]; then
  echo "Window \"$TITLE\" not found."
  exit 1
fi

# Remove window manager constraints
wmctrl -i -r "$WIN_ID" -b add,above,skip_taskbar,skip_pager

# Now move it
xdotool windowmove "$WIN_ID" "$X" "$Y"
