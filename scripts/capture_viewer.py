"""Render each viewer screen to PNG for visual review."""

from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(".witness-dev/tmp")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:7842"


def _click_tab(page, label: str) -> None:
    btn = page.get_by_role("button", name=label, exact=False).first
    btn.click()
    page.wait_for_timeout(450)


def _select_step(page, idx: int) -> None:
    btns = page.query_selector_all("div.w-\\[320px\\] ul li button")
    if idx < len(btns):
        btns[idx].click()
        page.wait_for_timeout(400)


def main() -> None:
    trace_id = sys.argv[1] if len(sys.argv) > 1 else None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
        )
        page = ctx.new_page()

        # 1. Trace list
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_selector("table", timeout=5000)
        page.screenshot(path=str(OUT / "01_list.png"))
        print("wrote 01_list.png")

        if not trace_id:
            rows = page.query_selector_all("tr a[href^='/traces/']")
            for a in rows:
                text = a.inner_text()
                if "amazing.com" in text:
                    trace_id = (a.get_attribute("href") or "").split("/")[-1]
                    break
            if not trace_id and rows:
                trace_id = (rows[0].get_attribute("href") or "").split("/")[-1]

        if not trace_id:
            print("no trace id — aborting")
            return

        page.goto(f"{BASE}/traces/{trace_id}", wait_until="networkidle")
        page.wait_for_timeout(1500)

        # Screenshots + Action + LLM: step 5 (add-to-cart — best visual compare)
        _select_step(page, 5)

        _click_tab(page, "Screenshots")
        page.screenshot(path=str(OUT / "02_detail_screenshots.png"))
        print("wrote 02_detail_screenshots.png")

        _click_tab(page, "Action")
        page.screenshot(path=str(OUT / "04_detail_action.png"))
        print("wrote 04_detail_action.png")

        _click_tab(page, "LLM Calls")
        page.screenshot(path=str(OUT / "05_detail_llm.png"))
        print("wrote 05_detail_llm.png")

        row = page.query_selector("tbody tr")
        if row:
            row.click()
            page.wait_for_timeout(400)
            page.screenshot(path=str(OUT / "06_detail_llm_modal.png"))
            print("wrote 06_detail_llm_modal.png")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

        # DOM Diff: step 0 (blank → full page) gives the cleanest side-by-side.
        _select_step(page, 0)
        _click_tab(page, "DOM Diff")
        page.screenshot(path=str(OUT / "03_detail_dom.png"))
        print("wrote 03_detail_dom.png")

        # Error state on the gmail trace
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_timeout(600)
        err_link = None
        for a in page.query_selector_all("tr a[href^='/traces/']"):
            if "Gmail" in a.inner_text():
                err_link = (a.get_attribute("href") or "").split("/")[-1]
                break
        if err_link:
            page.goto(f"{BASE}/traces/{err_link}", wait_until="networkidle")
            page.wait_for_timeout(1200)
            btns = page.query_selector_all("div.w-\\[320px\\] ul li button")
            if btns:
                btns[-1].click()
                page.wait_for_timeout(400)
            page.screenshot(path=str(OUT / "07_error_state.png"))
            print("wrote 07_error_state.png")

        browser.close()


if __name__ == "__main__":
    main()
