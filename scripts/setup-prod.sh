#!/usr/bin/env bash

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" manage.py migrate
"$PYTHON_BIN" manage.py bootstrap_data --env prod
"$PYTHON_BIN" manage.py check
