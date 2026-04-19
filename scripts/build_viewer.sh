#!/usr/bin/env bash
# Build the Witness viewer and copy it into the Python package.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/viewer"

if [[ ! -d node_modules ]]; then
  echo "[witness] installing viewer npm deps…"
  npm install --silent
fi

echo "[witness] building viewer…"
npx vite build

DEST="$ROOT/witness/_viewer_dist"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r dist/. "$DEST/"

echo "[witness] viewer built: $DEST"
