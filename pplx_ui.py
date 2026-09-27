#!/usr/bin/env python3
"""Drive Perplexity's web UI in your own Chrome, on your own Mac.

This is the shared core behind pplx_billing.py and pplx_connector.py, and a
small CLI of its own. Everything runs locally with a dedicated, persistent
Chrome profile (~/.pplx-billing-profile): sign in once, and the session sticks.
Nothing here is a Perplexity API; it is text/role-based UI automation that
hands the page to you whenever a locator misses.

CLI
  python pplx_ui.py login                  # one-time sign-in, saved to the profile
  python pplx_ui.py open connectors        # open a known page (see PAGES) and hand off
  python pplx_ui.py open /settings/api     # ...or any perplexity.ai path / full URL
  python pplx_ui.py shot usage --out u.png # screenshot a page and exit

Flags
  --pause      drop into Playwright Inspector at the hand-off instead of waiting
  --headless   not recommended; many settings widgets need a visible window

Library
  with browser(headless=False) as page:
      ensure_logged_in(page)
      goto(page, "connectors")
      click_first(page, [r"custom connector"])
      fill_first(page, [r"^name$"], "Filesystem Bridge")
      handoff(page, "Review, then click Add yourself.")

Secrets come from macOS Keychain via keychain_secret(service) and never pass
through chat, a tunnel, or a third party.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

BASE = "https://www.perplexity.ai"
PROFILE_DIR = Path(os.getenv("PPLX_PROFILE_DIR", Path.home() / ".pplx-billing-profile"))
HANDOFF_MS = int(os.getenv("PPLX_HANDOFF_MINUTES", "10")) * 60 * 1000

# Named pages. `open <name>` and goto(page, <name>) accept these keys, a
# perplexity.ai path (/settings/...), or a full https:// URL.
PAGES = {
    "home": f"{BASE}/",
    "usage": f"{BASE}/account/usage",
    "account": f"{BASE}/settings/account",
    "connectors": f"{BASE}/account/connectors",
    "api": f"{BASE}/settings/api",
    "library": f"{BASE}/library",
}

BUTTON = re.compile


def log(msg: str) -> None:
    print(f"[pplx-ui] {msg}", flush=True)


def resolve_url(target: str) -> str:
    if target in PAGES:
        return PAGES[target]
    if target.startswith("http://") or target.startswith("https://"):
        return target
    return BASE + ("/" + target.lstrip("/"))


# ---------- locating and acting ---------------------------------------------

def click_first(page: Page, patterns: list[str], timeout_ms: int = 8000,
                roles: tuple[str, ...] = ("button", "link", "menuitem", "tab", "option")) -> bool:
    """Click the first visible element whose accessible name matches any pattern."""
    for pat in patterns:
        for role in roles:
            loc = page.get_by_role(role, name=BUTTON(pat, re.I))
            try:
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                loc.first.click()
                log(f"clicked {role} /{pat}/")
                return True
            except PWTimeout:
                continue
    return False


def click_text(page: Page, patterns: list[str], timeout_ms: int = 5000) -> bool:
    """Fallback for controls with no role: click the first visible text match."""
    for pat in patterns:
        loc = page.get_by_text(BUTTON(pat, re.I))
        try:
            loc.first.wait_for(state="visible", timeout=timeout_ms)
            loc.first.click()
            log(f"clicked text /{pat}/")
            return True
        except PWTimeout:
            continue
    return False


def fill_first(page: Page, patterns: list[str], value: str, timeout_ms: int = 5000,
               secret: bool = False) -> bool:
    """Fill the first visible input matched by label, placeholder, or role name."""
    shown = "********" if secret else value
    for pat in patterns:
        rx = BUTTON(pat, re.I)
        candidates = (
            page.get_by_label(rx),
            page.get_by_placeholder(rx),
            page.get_by_role("textbox", name=rx),
        )
        for loc in candidates:
            try:
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                loc.first.click()
                loc.first.fill(value)
                log(f"filled /{pat}/ = {shown}")
                return True
            except PWTimeout:
                continue
    return False


def choose(page: Page, field_patterns: list[str], option_patterns: list[str],
           timeout_ms: int = 5000) -> bool:
    """Pick an option in a native <select>, radio group, or custom dropdown."""
    for fpat in field_patterns:
        rx = BUTTON(fpat, re.I)
        # Native select.
        sel = page.get_by_label(rx)
        try:
            sel.first.wait_for(state="visible", timeout=timeout_ms)
            if sel.first.evaluate("el => el.tagName") == "SELECT":
                for opat in option_patterns:
                    try:
                        sel.first.select_option(label=BUTTON(opat, re.I))
                        log(f"selected /{opat}/ in /{fpat}/")
                        return True
                    except Exception:
                        continue
        except PWTimeout:
            pass
    # Radio / segmented control labelled with the option text itself.
    if click_first(page, option_patterns, timeout_ms, roles=("radio", "tab", "option")):
        return True
    # Custom dropdown: open the trigger, then click the option.
    if click_first(page, field_patterns, timeout_ms, roles=("combobox", "button")):
        page.wait_for_timeout(400)
        if click_first(page, option_patterns, timeout_ms, roles=("option", "menuitem", "button")):
            return True
        return click_text(page, option_patterns, timeout_ms)
    return False


def check_first(page: Page, patterns: list[str], timeout_ms: int = 5000) -> bool:
    """Tick the first checkbox matched by label or accessible name."""
    for pat in patterns:
        rx = BUTTON(pat, re.I)
        for loc in (page.get_by_role("checkbox", name=rx), page.get_by_label(rx)):
            try:
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                if not loc.first.is_checked():
                    loc.first.check()
                log(f"checked /{pat}/")
                return True
            except PWTimeout:
                continue
            except Exception:
                # Custom checkbox that rejects check(); click the label instead.
                try:
                    loc.first.click()
                    log(f"clicked checkbox /{pat}/")
                    return True
                except Exception:
                    continue
    return False


# ---------- session --------------------------------------------------------

def goto(page: Page, target: str, settle_ms: int = 1500) -> None:
    page.goto(resolve_url(target), wait_until="domcontentloaded")
    page.wait_for_timeout(settle_ms)


def ensure_logged_in(page: Page, landing: str = "usage") -> None:
    goto(page, landing)
    if re.search(r"/(auth|signin|login)", page.url) or page.get_by_role(
            "button", name=BUTTON(r"continue with|sign in|log in", re.I)).count():
        log("Not signed in. Complete sign-in in the Chrome window (magic link / code)...")
        page.wait_for_url(re.compile(r"perplexity\.ai/(account|settings|library|$)"), timeout=HANDOFF_MS)
        goto(page, landing)
        log("Signed in; session saved to profile.")


def handoff(page: Page, msg: str, pause: bool = False) -> None:
    """Leave the window to the user: Inspector if --pause, else wait then close."""
    log(msg)
    if pause:
        page.pause()
    else:
        log(f"Window stays open for {HANDOFF_MS // 60000} min (PPLX_HANDOFF_MINUTES), then closes.")
        page.wait_for_timeout(HANDOFF_MS)


def screenshot(page: Page, out: str) -> str:
    path = str(Path(out).expanduser())
    page.screenshot(path=path, full_page=True)
    log(f"screenshot -> {path}")
    return path


# ---------- secrets ----------------------------------------------------------

def keychain_secret(service: str, account: str | None = None, as_json: bool = False):
    """Read a generic password from macOS Keychain. Never logs the value."""
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    if account:
        cmd[3:3] = ["-a", account]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        raise SystemExit(
            f"Keychain item '{service}' not found. Store it once in Terminal:\n"
            f"  security add-generic-password -a pplx -s {service} -U -w '<value>'")
    return json.loads(out) if as_json else out


# ---------- browser lifecycle -------------------------------------------------

@contextmanager
def browser(headless: bool = False) -> Iterator[Page]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield page
        finally:
            time.sleep(1)
            ctx.close()


def base_parser(doc: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pause", action="store_true", help="open Playwright Inspector at the hand-off")
    ap.add_argument("--headless", action="store_true")
    return ap


# ---------- CLI ------------------------------------------------------------------

def main() -> None:
    ap = base_parser(__doc__)
    ap.add_argument("command", choices=["login", "open", "shot"])
    ap.add_argument("target", nargs="?", default="usage",
                    help="page name (%s), perplexity.ai path, or URL" % ", ".join(PAGES))
    ap.add_argument("--out", default="pplx.png", help="screenshot path for 'shot'")
    a = ap.parse_args()

    with browser(headless=a.headless) as page:
        if a.command == "login":
            ensure_logged_in(page)
            log("Done. You can close the window.")
        elif a.command == "open":
            ensure_logged_in(page)
            goto(page, a.target)
            handoff(page, f"Opened {resolve_url(a.target)}.", a.pause)
        elif a.command == "shot":
            ensure_logged_in(page)
            goto(page, a.target, settle_ms=2500)
            screenshot(page, a.out)


if __name__ == "__main__":
    main()
