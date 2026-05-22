#!/bin/bash
set -a
source "$(dirname "$0")/.env"
set +a
exec /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 "$(dirname "$0")/intraday_run.py"
