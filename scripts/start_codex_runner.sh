#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p runtime
exec /usr/bin/env python3 scripts/codex_runner.py "$@"

