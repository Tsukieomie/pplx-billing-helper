#!/usr/bin/env python3
"""Local automation for Perplexity billing chores that no API exposes.

Runs a real Chrome on YOUR Mac with a dedicated, persistent profile, so:
  * you log in once (magic link / code), and the session sticks;
  * card digits never leave your machine — they come from macOS Keychain
    (or you type them into the Stripe form yourself when the script pauses).

Commands
  python pplx_billing.py login            # one-time: open Perplexity, sign in, close window
  python pplx_billing.py card             # navigate to the add-payment-method form
  python pplx_billing.py card --keychain  # ...and fill it from Keychain item 'pplx-billing-card'
  python pplx_billing.py buy [--credits N] # open the buy-credits dialog (and pick amount N)
  python pplx_billing.py refill           # open the auto-refill controls

Flags
  --pause      drop into Playwright Inspector at the end instead of auto-closing
  --headless   (not recommended; Stripe frames often need a visible window)

Keychain item format (one-time setup, done in Terminal, never in chat):
  security add-generic-password -a pplx -s pplx-billing-card -U \
    -w '{"number":"4242424242424242","exp":"12/30","cvc":"123","zip":"02903"}'

Perplexity's account UI changes; every locator below is text/role based and
falls back to pausing so you can finish by hand. Nothing here is a Perplexity API.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

PROFILE_DIR = Path(os.getenv("PPLX_PROFILE_DIR", Path.home() / ".pplx-billing-profile"))
USAGE_URL = "https://www.perplexity.ai/account/usage"
ACCOUNT_URL = "https://www.perplexity.ai/settings/account"
BUTTON = re.compile


def log(msg: str) -> None:
    print(f"[pplx-billing] {msg}", flush=True)


# ---------- helpers -------------------------------------------------------

def click_first(page: Page, patterns: list[str], timeout_ms: int = 8000) -> bool:
    """Click the first visible button/link whose text matches any pattern."""
    for pat in patterns:
        for role in ("button", "link", "menuitem"):
            loc = page.get_by_role(role, name=BUTTON(pat, re.I))
            try:
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                loc.first.click()
                log(f"clicked {role} /{pat}/")
                return True
            except PWTimeout:
                continue
    return False


def ensure_logged_in(page: Page) -> None:
    page.goto(USAGE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    if re.search(r"/(auth|signin|login)", page.url) or page.get_by_role(
            "button", name=BUTTON(r"continue with|sign in|log in", re.I)).count():
        log("Not signed in. Complete sign-in in the Chrome window (magic link / code)...")
        page.wait_for_url(re.compile(r"perplexity\.ai/(account|settings|$)"), timeout=10 * 60 * 1000)
        page.goto(USAGE_URL, wait_until="domcontentloaded")
        log("Signed in; session saved to profile.")


def keychain_card() -> dict:
    out = subprocess.run(
        ["security", "find-generic-password", "-s", "pplx-billing-card", "-w"],
        capture_output=True, text=True, check=True).stdout.strip()
    card = json.loads(out)
    for k in ("number", "exp", "cvc"):
        if k not in card:
            raise SystemExit(f"Keychain item missing '{k}'")
    return card


def fill_stripe_card(page: Page, card: dict) -> bool:
    """Fill Stripe Elements / Payment Element iframes. Returns True if number field found."""
    page.wait_for_timeout(2000)
    frames = page.frame_locator("iframe[name^='__privateStripeFrame'], iframe[src*='js.stripe.com']")
    candidates = [
        ("number", ["input[name='cardnumber']", "input[name='number']", "input[autocomplete='cc-number']"]),
        ("exp",    ["input[name='exp-date']", "input[name='expiry']", "input[autocomplete='cc-exp']"]),
        ("cvc",    ["input[name='cvc']", "input[autocomplete='cc-csc']"]),
        ("zip",    ["input[name='postal']", "input[name='postalCode']", "input[autocomplete='postal-code']"]),
    ]
    filled_number = False
    for key, selectors in candidates:
        val = card.get(key)
        if not val:
            continue
        for sel in selectors:
            try:
                loc = frames.locator(sel).first
                loc.wait_for(state="visible", timeout=4000)
                loc.click()
                loc.type(str(val), delay=40)
                log(f"filled {key}")
                if key == "number":
                    filled_number = True
                break
            except PWTimeout:
                continue
    return filled_number


# ---------- commands ------------------------------------------------------

def cmd_login(page: Page, _a) -> None:
    ensure_logged_in(page)
    log("Done. You can close the window.")


def cmd_card(page: Page, a) -> None:
    ensure_logged_in(page)
    # Path 1: credit payment-method control on the usage page (separate from subscription card).
    if not click_first(page, [r"payment method", r"add (a )?card", r"change card", r"update (payment|card)"], 6000):
        # Path 2: subscription Stripe portal.
        page.goto(ACCOUNT_URL, wait_until="domcontentloaded")
        if click_first(page, [r"manage (plan|subscription)"], 8000):
            page.wait_for_timeout(3000)
            click_first(page, [r"add payment method", r"update payment method", r"\+ ?add"], 10000)
    if a.keychain:
        card = keychain_card()
        if fill_stripe_card(page, card):
            log("Card fields filled from Keychain. Review the form, then click Save/Add yourself "
                "(the script never submits a payment form).")
        else:
            log("Couldn't find Stripe card fields automatically — form is open, finish by hand.")
    else:
        log("Card form should be open. Type the card in the window; nothing was auto-filled.")
    page.pause() if a.pause else page.wait_for_timeout(10 * 60 * 1000)


def cmd_buy(page: Page, a) -> None:
    ensure_logged_in(page)
    if not click_first(page, [r"buy (more )?credits", r"purchase credits", r"add credits", r"top ?up"], 10000):
        log("No buy-credits button found; page left open for you.")
    elif a.credits:
        page.wait_for_timeout(1500)
        if not click_first(page, [rf"\b{a.credits:,}\b", rf"\b{a.credits}\b"], 5000):
            log(f"Couldn't pick {a.credits}; choose an amount in the window.")
    log("Confirm the purchase in the window. The script does not press the final Pay button.")
    page.pause() if a.pause else page.wait_for_timeout(10 * 60 * 1000)


def cmd_refill(page: Page, a) -> None:
    ensure_logged_in(page)
    try:
        page.get_by_text(BUTTON(r"auto[- ]?refill", re.I)).first.scroll_into_view_if_needed(timeout=8000)
        log("Auto-refill controls in view. Toggle on, set refill amount and monthly cap.")
    except PWTimeout:
        log("Auto-refill section not found; page left open.")
    page.pause() if a.pause else page.wait_for_timeout(10 * 60 * 1000)


COMMANDS = {"login": cmd_login, "card": cmd_card, "buy": cmd_buy, "refill": cmd_refill}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--keychain", action="store_true", help="fill card from Keychain item pplx-billing-card")
    ap.add_argument("--credits", type=int, help="credit pack size to pre-select for 'buy'")
    ap.add_argument("--pause", action="store_true", help="open Playwright Inspector at the end")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=a.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            COMMANDS[a.command](page, a)
        finally:
            time.sleep(1)
            ctx.close()


if __name__ == "__main__":
    main()
