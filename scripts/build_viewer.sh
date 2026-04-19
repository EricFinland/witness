#!/usr/bin/env bash
# Delegates to scripts/build_viewer.py so both bash and Windows users use the
# same path. All logic lives in the Python version.
set -euo pipefail
exec python "$(dirname "$0")/build_viewer.py" "$@"
