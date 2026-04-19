"""Build the Witness viewer and copy it into the Python package.

Works on Windows, macOS, and Linux. Requires Node.js + npm on PATH.

    python scripts/build_viewer.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "viewer"
DIST = VIEWER / "dist"
DEST = ROOT / "witness" / "_viewer_dist"


def run(cmd: list[str], cwd: Path) -> None:
    print(f"[witness] $ {' '.join(cmd)}")
    # shell=True on Windows so "npm.cmd" / "npx.cmd" resolve without us guessing.
    use_shell = sys.platform == "win32"
    result = subprocess.run(
        cmd if not use_shell else " ".join(cmd),
        cwd=str(cwd),
        shell=use_shell,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    if not shutil.which("npm" if sys.platform != "win32" else "npm.cmd") and not shutil.which("npm"):
        print(
            "[witness] npm not found on PATH. Install Node.js 20+ to build the viewer.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not (VIEWER / "node_modules").exists():
        print("[witness] installing viewer npm deps (first run) …")
        run(["npm", "install", "--silent"], VIEWER)

    print("[witness] building viewer …")
    run(["npx", "vite", "build"], VIEWER)

    if not DIST.exists():
        print(f"[witness] expected {DIST} to exist after build", file=sys.stderr)
        sys.exit(1)

    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(DIST, DEST)

    print(f"[witness] viewer built → {DEST}")


if __name__ == "__main__":
    main()
