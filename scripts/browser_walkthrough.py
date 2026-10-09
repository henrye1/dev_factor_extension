"""Drive the running app in a real browser: sign in, create a project, upload the seven
zips, create and run scenarios, and save a screenshot of each page.

Needs the app running locally and `pip install playwright` (uses the installed Edge).

Usage:  python scripts/browser_walkthrough.py <base-url> <email> <password> <screenshot-dir>
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ZIPS = [ROOT / f"debug ({n}).zip" for n in ("11", "15", "22", "23", "25", "44", "ALL")]


def main(base: str, email: str, password: str, out: str) -> int:
    shots = Path(out)
    shots.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        # the first /auth/me before signing in is a 401 by design
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
                if m.type in ("error", "warning") and "401" not in m.text else None)
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 and "/auth/me" not in r.url else None)

        def shot(name: str, full: bool = True):
            page.wait_for_timeout(500)
            page.screenshot(path=str(shots / f"{name}.png"), full_page=full)

        page.goto(base)
        page.get_by_label("Email", exact=True).fill(email)
        page.get_by_label("Password", exact=True).fill(password)
        shot("01_signin")
        page.get_by_role("button", name="Sign in").click()
        page.get_by_role("heading", name="Projects").wait_for()

        page.get_by_role("button", name="New project").click()
        page.get_by_label("Name", exact=True).fill(f"Nutun July 2026 ({time.strftime('%H%M%S')})")
        page.get_by_role("button", name="Create project").click()
        page.get_by_role("heading", name="Zips").wait_for()

        page.set_input_files("input[type=file]", [str(z) for z in ZIPS])
        page.locator(".uploads li.good").nth(len(ZIPS) - 1).wait_for(timeout=180_000)
        shot("02_uploaded")
        page.get_by_role("heading", name="Scenarios").wait_for()
        page.wait_for_timeout(1800)

        # scenario 1: workbook defaults with a longer horizon
        page.get_by_role("button", name="New scenario").click()
        page.get_by_label("Name", exact=True).fill("Log-normal 360")
        page.get_by_role("button", name="Create scenario").click()
        page.get_by_label("Target TermStep", exact=True).wait_for()
        page.get_by_label("Target TermStep", exact=True).fill("360")
        page.get_by_label("MaxBucket", exact=True).fill("480")
        page.locator("select#p_method").select_option("3")
        page.get_by_role("button", name="Save changes").click()
        page.get_by_text("Scenario saved").wait_for()
        page.wait_for_timeout(600)
        # give the ALL zip the last 10 years of vintages
        page.locator("tr", has_text="VBALL").get_by_role("button", name="Edit override").click()
        page.get_by_label("Override Vintages").check()
        page.locator("dialog select#p_vintages_o").select_option("years")
        page.locator("dialog").get_by_label("Number of years", exact=True).fill("10")
        shot("03_override_dialog", full=False)
        page.get_by_role("button", name="Save override").click()
        page.get_by_text("Override saved").wait_for()
        page.wait_for_timeout(600)
        page.get_by_role("button", name="Save and run all zips").click()
        page.get_by_text("7 of 7 zips run").wait_for(timeout=600_000)
        shot("04_scenario")

        # scenario 2: a copy using the exponential shape and a percentage credibility cut
        page.locator(".crumbs a").nth(1).click()
        page.get_by_role("heading", name="Scenarios").wait_for()
        page.locator("tr", has_text="Log-normal 360").get_by_role("button", name="Copy").click()
        page.get_by_label("Name of the copy", exact=True).fill("Exponential, 0.5% cut")
        page.get_by_role("button", name="Copy scenario").click()
        page.get_by_label("Method", exact=True).wait_for()
        page.locator("select#p_method").select_option("1")
        page.locator("select#p_min_exposure_mode").select_option("pct")
        page.get_by_label("MinExposure (credibility cut)", exact=True).fill("0.5")
        page.locator("select#p_vintages").select_option("years")
        page.get_by_label("Number of years", exact=True).fill("10")
        page.get_by_role("button", name="Save and run all zips").click()
        page.get_by_text("7 of 7 zips run").wait_for(timeout=600_000)

        page.locator(".crumbs a").nth(1).click()
        page.locator("table.matrix").wait_for()
        shot("05_project")

        page.locator("table.matrix tr", has_text="VB44").locator("td.cell").first.click()
        page.locator(".chart svg path").first.wait_for()
        page.wait_for_timeout(1500)
        shot("06_zip_44")
        page.get_by_label("TermStep", exact=True).fill("48")
        page.get_by_label("TermStep", exact=True).press("Enter")
        page.wait_for_timeout(1000)
        page.locator(".chart").nth(2).scroll_into_view_if_needed()
        box = page.locator(".chart").nth(2).locator("svg").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.5)
        shot("07_zip_44_hover", full=False)
        page.get_by_role("tab", name="Results by TermStep").click()
        page.get_by_role("tab", name="Results by TermStep").scroll_into_view_if_needed()
        shot("08_zip_44_table", full=False)

        page.locator(".crumbs a").nth(1).click()
        page.locator("table.matrix tr", has_text="VBALL").locator("td.cell").first.click()
        page.locator(".chart svg path").first.wait_for()
        page.wait_for_timeout(1500)
        shot("09_zip_all")

        page.locator(".crumbs a").nth(1).click()
        page.get_by_role("link", name="Members and curves").click()
        page.get_by_role("heading", name="Compare the client's curves with our fitted tails").wait_for()
        page.locator(".chart svg path").first.wait_for()
        shot("10_settings")

        page.set_viewport_size({"width": 420, "height": 900})
        page.locator(".crumbs a").nth(1).click()
        page.locator("table.matrix").wait_for()
        shot("11_project_narrow", full=False)

        browser.close()
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s); screenshots in {shots}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
