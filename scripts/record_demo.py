"""Record the 6-second "hero GIF" for the README.

Drives the viewer with Playwright while recording a WebM, then (optionally)
converts to GIF via ffmpeg if it's on PATH. The WebM is always written so
you can compress it yourself with Kap / Cleanshot / gifski — many people
prefer the handheld tools.

Prereqs:
    witness view     # running in another shell on :7842
    python scripts/seed_demo.py   # at least one rich trace seeded
    (optional) ffmpeg + gifski on PATH for auto-GIF

Writes:
    .witness-dev/tmp/hero.webm   — always
    .witness-dev/tmp/hero.gif    — if ffmpeg available
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(".witness-dev/tmp")
OUT.mkdir(parents=True, exist_ok=True)
WEBM = OUT / "hero.webm"
GIF = OUT / "hero.gif"
BASE = "http://127.0.0.1:7842"


def record() -> None:
    """Run the scripted viewer tour and save the video."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 720},
            device_scale_factor=1,
            record_video_dir=str(OUT),
            record_video_size={"width": 1280, "height": 720},
        )
        page = ctx.new_page()

        # 0.0 – 1.5s : trace list lands
        page.goto(BASE + "/", wait_until="networkidle")
        time.sleep(1.5)

        # 1.5 – 2.3s : click the 11-step amazing.com trace
        for a in page.query_selector_all("tr a[href^='/traces/']"):
            if "amazing.com" in a.inner_text():
                a.click()
                break
        page.wait_for_timeout(900)

        # 2.3 – 4.2s : step through with j (six presses)
        for _ in range(6):
            page.keyboard.press("j")
            page.wait_for_timeout(280)

        # 4.2 – 5.4s : switch to DOM diff (key 2), let it render
        page.keyboard.press("2")
        page.wait_for_timeout(1200)

        # 5.4 – 6.0s : switch to LLM (key 4) and linger
        page.keyboard.press("4")
        page.wait_for_timeout(600)

        ctx.close()  # flushes the video
        browser.close()

    # Playwright writes a randomized filename; grab the newest .webm and rename.
    vids = sorted(OUT.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not vids:
        print("no video written", file=sys.stderr)
        sys.exit(1)
    latest = vids[0]
    if latest != WEBM:
        if WEBM.exists():
            WEBM.unlink()
        latest.rename(WEBM)
    print(f"wrote {WEBM}")


def convert_to_gif() -> None:
    """Try ffmpeg → palette → gif for a reasonably small file."""
    if not shutil.which("ffmpeg"):
        print(
            "ffmpeg not on PATH; skipping GIF conversion.\n"
            "  - macOS: brew install ffmpeg\n"
            "  - Windows: winget install Gyan.FFmpeg\n"
            "  - Or compress hero.webm yourself with Kap/Cleanshot/gifski."
        )
        return

    palette = OUT / "hero.palette.png"
    fps = "15"
    width = "960"  # downscale for README

    subprocess.check_call(
        [
            "ffmpeg", "-y", "-i", str(WEBM),
            "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen",
            str(palette),
        ]
    )
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-i", str(WEBM), "-i", str(palette),
            "-filter_complex",
            f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse",
            str(GIF),
        ]
    )
    size_mb = GIF.stat().st_size / (1024 * 1024)
    print(f"wrote {GIF} ({size_mb:.2f} MB)")
    palette.unlink(missing_ok=True)


if __name__ == "__main__":
    record()
    convert_to_gif()
