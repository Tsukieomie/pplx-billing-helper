#!/usr/bin/env python3
"""Print your Perplexity Computer credit balance from the terminal.

Uses the same read-only endpoint CodexBar and friends use:
    GET https://www.perplexity.ai/rest/billing/credits?version=2.18&source=default
authenticated with your existing browser session cookie. Nothing is written.

Cookie resolution order:
  1. $PERPLEXITY_COOKIE        full "Cookie:" header value
  2. $PERPLEXITY_SESSION_TOKEN bare session-token value
  3. Chrome / Safari / Firefox cookies for www.perplexity.ai (via browser-cookie3)

Exit code 0 = ok, 2 = not logged in, 1 = other error.
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests

URL = "https://www.perplexity.ai/rest/billing/credits?version=2.18&source=default"
SESSION_COOKIE_NAMES = (
    "__Secure-authjs.session-token",
    "authjs.session-token",
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
)


def resolve_cookie() -> str | None:
    if os.getenv("PERPLEXITY_COOKIE"):
        return os.environ["PERPLEXITY_COOKIE"]
    if os.getenv("PERPLEXITY_SESSION_TOKEN"):
        return f"{SESSION_COOKIE_NAMES[0]}={os.environ['PERPLEXITY_SESSION_TOKEN']}"
    try:
        import browser_cookie3  # type: ignore
    except ImportError:
        return None
    for loader in (browser_cookie3.chrome, browser_cookie3.safari, browser_cookie3.firefox,
                   browser_cookie3.brave, browser_cookie3.edge):
        try:
            jar = loader(domain_name="perplexity.ai")
        except Exception:
            continue
        pairs = [f"{c.name}={c.value}" for c in jar if c.name in SESSION_COOKIE_NAMES or c.name.startswith("__Secure")]
        if pairs:
            return "; ".join(pairs)
    return None


def cents(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    cookie = resolve_cookie()
    if not cookie:
        print("No Perplexity session cookie found. Sign in at perplexity.ai in Chrome/Safari, "
              "or export PERPLEXITY_COOKIE / PERPLEXITY_SESSION_TOKEN.", file=sys.stderr)
        return 2

    r = requests.get(URL, headers={
        "Cookie": cookie,
        "Origin": "https://www.perplexity.ai",
        "Referer": "https://www.perplexity.ai/account/usage",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    }, timeout=20)
    if r.status_code in (401, 403):
        print("Session cookie rejected — log out/in to perplexity.ai and retry.", file=sys.stderr)
        return 2
    r.raise_for_status()
    data = r.json()

    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
        return 0

    grants = data.get("credit_grants") or data.get("creditGrants") or []
    now = time.time()
    recurring = sum(cents(g.get("amount_cents")) for g in grants if g.get("type") == "recurring")
    promo = sum(cents(g.get("amount_cents")) for g in grants
                if g.get("type") == "promotional" and cents(g.get("expires_at_ts") or float("inf")) > now)
    purchased = max(sum(cents(g.get("amount_cents")) for g in grants if g.get("type") == "purchased"),
                    cents(data.get("current_period_purchased_cents")))
    used = cents(data.get("total_usage_cents"))

    # Perplexity consumes recurring -> purchased -> bonus
    rec_used = min(used, recurring); used -= rec_used
    pur_used = min(used, purchased); used -= pur_used
    promo_used = min(used, promo)

    total_left = (recurring - rec_used) + (purchased - pur_used) + (promo - promo_used)
    renewal = data.get("renewal_date_ts")
    renewal_s = time.strftime("%Y-%m-%d", time.localtime(renewal)) if renewal else "n/a"

    print(f"Recurring : {recurring - rec_used:>8.0f} / {recurring:.0f}   (renews {renewal_s})")
    print(f"Purchased : {purchased - pur_used:>8.0f} / {purchased:.0f}")
    print(f"Bonus     : {promo - promo_used:>8.0f} / {promo:.0f}")
    print(f"TOTAL LEFT: {total_left:>8.0f} credits")
    threshold = float(os.getenv("PPLX_LOW_CREDITS", "500"))
    if total_left < threshold:
        print(f"\nLOW: below {threshold:.0f}. Run:  python pplx_billing.py buy", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
