#!/bin/bash
set -e

# Start Xvfb background virtual display
Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp &
export DISPLAY=:99

# Give Xvfb half a second to initialize display socket
sleep 1

# Start Gunicorn directly so it binds to Render's dynamic $PORT
exec gunicorn --timeout 120 --bind 0.0.0.0:${PORT:-10000} wsgi:app
