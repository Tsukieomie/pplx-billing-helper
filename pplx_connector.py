#!/usr/bin/env python3
"""Add or inspect custom connectors in Perplexity's account settings, locally.

Fills the "+ Custom connector → Remote" form the way you would by hand,
in your own Chrome, then stops. You review the form and click Add. The
API key comes from macOS Keychain (or --api-key-stdin) and is never echoed.

Commands
  python pplx_connector.py list
  python pplx_connector.py add --name "Filesystem Bridge" \
      --url https://your-host.example/mcp --auth api-key --keychain pplx-connector-key
  python pplx_connector.py add --name "Public MCP" --url https://x.example/sse \
      --auth none --transport sse --description "read-only docs"

Flags
  --keychain SERVICE   Keychain item holding the API key (default pplx-connector-key)
  --api-key-stdin      read the API key from stdin instead of Keychain
  --shot PATH          screenshot the filled form before handing off
  --pause              open Playwright Inspector at the hand-off

Store the key once, in Terminal (not in any chat):
  security add-generic-password -a pplx -s pplx-connector-key -U -w '<your key>'

UI path (per Perplexity Help Center, "Adding Custom Remote Connectors"):
  Account settings → Connectors → + Custom connector → Remote →
  Name, MCP Server URL (https required), Description, Authentication
  (OAuth / API Key / None), Transport (Streamable HTTP / SSE), Icon →
  acknowledgement checkbox → Add → click the card to authenticate/enable.
"""
from __future__ import annotations

import re
import sys

from playwright.sync_api import Page

from pplx_ui import (base_parser, browser, check_first, choose, click_first,
                     click_text, ensure_logged_in, fill_first, goto, handoff,
                     keychain_secret, log, screenshot)

AUTH_LABEL = {"api-key": r"api ?key", "oauth": r"oauth", "none": r"^none$|no auth"}
TRANSPORT_LABEL = {"http": r"streamable ?http", "sse": r"^sse$|server[- ]sent"}


def open_connectors(page: Page) -> None:
    ensure_logged_in(page, landing="connectors")
    if not re.search(r"/connectors", page.url):
        # Older layout: connectors live under Account settings.
        goto(page, "account")
        click_first(page, [r"^connectors$"], 6000)


def cmd_list(page: Page, a) -> None:
    open_connectors(page)
    page.wait_for_timeout(1500)
    cards = page.get_by_role("heading").all_inner_texts()
    names = [t.strip() for t in cards if t.strip() and len(t.strip()) < 60]
    if names:
        log("Visible connector / section headings:")
        for n in names:
            print("  -", n)
    else:
        log("No headings found; the page is open for you to look at.")
    if a.shot:
        screenshot(page, a.shot)
    handoff(page, "Connectors page open.", a.pause)


def cmd_add(page: Page, a) -> None:
    if not a.url.lower().startswith("https://"):
        raise SystemExit("MCP Server URL must be https:// (Perplexity rejects plain http).")

    api_key = None
    if a.auth == "api-key":
        api_key = sys.stdin.readline().strip() if a.api_key_stdin else keychain_secret(a.keychain)
        if not api_key:
            raise SystemExit("Empty API key.")

    open_connectors(page)

    if not click_first(page, [r"\+?\s*custom connector", r"add (custom )?connector"], 10000):
        handoff(page, "Couldn't find '+ Custom connector'. Click it yourself, then rerun with --pause "
                      "or finish by hand.", a.pause)
        return
    page.wait_for_timeout(800)
    click_first(page, [r"^remote$"], 6000, roles=("button", "tab", "radio", "option", "menuitem")) \
        or click_text(page, [r"^remote$"], 4000)
    page.wait_for_timeout(600)

    ok_name = fill_first(page, [r"^name$", r"display name", r"connector name"], a.name)
    ok_url = fill_first(page, [r"mcp server url", r"server url", r"^url$"], a.url)
    if a.description:
        fill_first(page, [r"description"], a.description, 3000)

    ok_auth = choose(page, [r"authentication", r"^auth"], [AUTH_LABEL[a.auth]])
    if api_key:
        page.wait_for_timeout(500)
        if fill_first(page, [r"api ?key", r"token", r"secret"], api_key, 5000, secret=True):
            log("API key filled from Keychain (value not shown).")
        else:
            log("API key field not found; paste it yourself in the window.")
    ok_tr = choose(page, [r"transport"], [TRANSPORT_LABEL[a.transport]])
    ok_ack = check_first(page, [r"understand", r"acknowledge", r"risk"], 4000)

    missed = [k for k, v in (("Name", ok_name), ("MCP Server URL", ok_url), ("Authentication", ok_auth),
                             ("Transport", ok_tr), ("acknowledgement", ok_ack)) if not v]
    if a.shot:
        screenshot(page, a.shot)
    note = f" Fields to finish by hand: {', '.join(missed)}." if missed else ""
    handoff(page, "Form filled." + note + " Review it, then click Add yourself; afterwards click the new "
                  "card to authenticate and enable it. The script never presses Add.", a.pause)


COMMANDS = {"list": cmd_list, "add": cmd_add}


def main() -> None:
    ap = base_parser(__doc__)
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--name", help="connector display name")
    ap.add_argument("--url", help="MCP Server URL (https)")
    ap.add_argument("--description", default="")
    ap.add_argument("--auth", choices=AUTH_LABEL, default="api-key")
    ap.add_argument("--transport", choices=TRANSPORT_LABEL, default="http")
    ap.add_argument("--keychain", default="pplx-connector-key", help="Keychain service holding the API key")
    ap.add_argument("--api-key-stdin", action="store_true", help="read API key from stdin")
    ap.add_argument("--shot", help="save a screenshot of the form to this path")
    a = ap.parse_args()
    if a.command == "add" and not (a.name and a.url):
        ap.error("add requires --name and --url")

    with browser(headless=a.headless) as page:
        COMMANDS[a.command](page, a)


if __name__ == "__main__":
    main()
