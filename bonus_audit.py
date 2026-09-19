#!/usr/bin/env python3
"""Audit whether the one-time Computer bonus grant landed on your account,
and draft the support ticket if it didn't.

Read-only. Uses the same endpoint as check_credits.py:
    GET https://www.perplexity.ai/rest/billing/credits?version=2.18&source=default

Usage
  python bonus_audit.py                    # cookie from env / browser (see check_credits.py)
  python bonus_audit.py --proxy            # no cookie lookup; rely on an HTTPS proxy that injects auth
                                           # (this is how Perplexity Computer runs it with a vault credential)
  python bonus_audit.py --from credits.json  # audit a saved JSON dump instead of calling the API
  python bonus_audit.py --expect 8000      # Enterprise Pro bonus is 8,000; Max is 35,000
  python bonus_audit.py --out ./audit      # where to write credits.json + support_request.md

Exit codes: 0 bonus present, 3 bonus missing/expired (ticket drafted), 2 not logged in, 1 other.

Published bonus amounts (help center, Sept 2026):
  Consumer Pro 4,000 | Consumer Max 35,000 | Enterprise Pro 8,000 | Enterprise Max 40,000
Bonuses are one per account, expire 30 days after grant, and do not repeat on
cancel/resubscribe, monthly<->yearly switch, or later discounts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

URL = "https://www.perplexity.ai/rest/billing/credits?version=2.18&source=default"
HELP_URL = "https://www.perplexity.ai/help-center/en/articles/13838041-how-credits-work-on-perplexity"
BONUS_TYPES = {"promotional", "promo", "bonus", "signup", "sign_up", "onboarding"}
TS_KEYS_GRANTED = ("granted_at_ts", "created_at_ts", "start_ts", "starts_at_ts", "created_ts", "grant_ts")
TS_KEYS_EXPIRES = ("expires_at_ts", "expiry_ts", "expires_ts", "end_ts", "ends_at_ts")


def num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def first(d: dict, keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def fmt_ts(ts) -> str:
    ts = num(ts)
    if not ts:
        return "n/a"
    if ts > 1e12:  # milliseconds
        ts /= 1000
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def fetch(use_proxy: bool) -> dict:
    import requests

    headers = {
        "Origin": "https://www.perplexity.ai",
        "Referer": "https://www.perplexity.ai/account/usage",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept": "application/json",
    }
    if not use_proxy:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from check_credits import resolve_cookie  # type: ignore

        cookie = resolve_cookie()
        if not cookie:
            print("No Perplexity session cookie found (set PERPLEXITY_COOKIE or use --proxy).", file=sys.stderr)
            sys.exit(2)
        headers["Cookie"] = cookie
    r = requests.get(URL, headers=headers, timeout=30)
    if r.status_code in (401, 403):
        print(f"Endpoint rejected the session ({r.status_code}). Sign in again and retry.", file=sys.stderr)
        sys.exit(2)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        print("Non-JSON response (probably a login page). Sign in again and retry.", file=sys.stderr)
        sys.exit(2)


def normalise_grants(data: dict) -> list[dict]:
    grants = data.get("credit_grants") or data.get("creditGrants") or data.get("grants") or []
    out = []
    for g in grants:
        if not isinstance(g, dict):
            continue
        gtype = str(first(g, ("type", "grant_type", "source", "category"), "")).lower()
        amount = num(first(g, ("amount_cents", "amount", "total_cents", "granted_cents")))
        remaining = first(g, ("remaining_cents", "balance_cents", "remaining"))
        used = first(g, ("used_cents", "usage_cents", "used"))
        if remaining is None and used is not None:
            remaining = amount - num(used)
        out.append({
            "type": gtype,
            "amount": amount,
            "remaining": None if remaining is None else num(remaining),
            "granted_ts": num(first(g, TS_KEYS_GRANTED)),
            "expires_ts": num(first(g, TS_KEYS_EXPIRES)),
            "raw": g,
        })
    return out


def audit(data: dict, expect: int, now: float) -> tuple[bool, str, list[dict]]:
    grants = normalise_grants(data)
    bonus = [g for g in grants if g["type"] in BONUS_TYPES]
    lines = []
    lines.append("Credit grants on the account:")
    if not grants:
        lines.append("  (none returned)")
    for g in grants:
        exp = g["expires_ts"]
        status = "active" if not exp or exp > now else "EXPIRED"
        rem = "" if g["remaining"] is None else f", remaining {g['remaining']:.0f}"
        lines.append(f"  - {g['type'] or '?':<12} {g['amount']:>8.0f} credits{rem}  granted {fmt_ts(g['granted_ts'])}  expires {fmt_ts(exp)}  [{status}]")

    sub = data.get("subscription") or data.get("plan") or {}
    tier = first(data, ("subscription_tier", "tier", "plan_name"), None) or (sub.get("tier") if isinstance(sub, dict) else None)
    if tier:
        lines.append(f"Plan reported by endpoint: {tier}")
    if data.get("renewal_date_ts"):
        lines.append(f"Renewal: {fmt_ts(data['renewal_date_ts'])}")
    lines.append(f"Total usage recorded: {num(data.get('total_usage_cents')):.0f} credits")

    if not bonus:
        return False, "\n".join(lines) + f"\n\nVERDICT: no promotional/bonus grant exists on this account (expected {expect:,}).", grants
    active = [g for g in bonus if not g["expires_ts"] or g["expires_ts"] > now]
    total = sum(g["amount"] for g in bonus)
    if active:
        return True, "\n".join(lines) + f"\n\nVERDICT: bonus grant present ({total:.0f} credits, active).", grants
    return False, "\n".join(lines) + f"\n\nVERDICT: bonus grant of {total:.0f} credits was issued but has EXPIRED (30-day window).", grants


def draft_ticket(email: str, expect: int, report: str, grants: list[dict]) -> str:
    bonus = [g for g in grants if g["type"] in BONUS_TYPES]
    if bonus:
        situation = (
            f"the one-time {expect:,}-credit Computer bonus shows on my account as granted "
            f"{fmt_ts(bonus[0]['granted_ts'])} and expired {fmt_ts(bonus[0]['expires_ts'])}, "
            "before I had a chance to use it"
        )
        ask = "Could you re-issue the bonus with a fresh 30-day window?"
    else:
        situation = (
            f"the one-time {expect:,}-credit Computer bonus described on your pricing page and in the "
            "help center never appeared under Bonus credits on my usage page, and the billing endpoint "
            "returns no promotional grant for the account"
        )
        ask = "Could you check why the grant was never issued and apply it?"
    return f"""Subject: One-time Computer bonus credits missing

Hi Perplexity Support,

Account: {email}
Issue: {situation}.

This is the first paid subscription on this account (no cancel/resubscribe, no monthly-yearly switch,
no education discount), so per {HELP_URL} it should be eligible. {ask}

Evidence from GET /rest/billing/credits (read-only, pulled {time.strftime('%Y-%m-%d %H:%M %Z')}):

{report}

Thanks,
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy", action="store_true", help="skip cookie lookup; an HTTPS proxy injects auth")
    ap.add_argument("--from", dest="from_file", help="audit a saved JSON dump instead of calling the API")
    ap.add_argument("--expect", type=int, default=4000, help="expected bonus size (default 4000 = consumer Pro)")
    ap.add_argument("--email", default=os.getenv("PPLX_ACCOUNT_EMAIL", "<your account email>"))
    ap.add_argument("--out", default=".", help="directory for credits.json and support_request.md")
    ap.add_argument("--json", action="store_true", help="print the raw payload and exit")
    a = ap.parse_args()

    data = json.loads(Path(a.from_file).read_text()) if a.from_file else fetch(a.proxy)
    if a.json:
        print(json.dumps(data, indent=2))
        return 0

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "credits.json").write_text(json.dumps(data, indent=2))

    ok, report, grants = audit(data, a.expect, time.time())
    print(report)
    if ok:
        return 0
    ticket = draft_ticket(a.email, a.expect, report, grants)
    (out / "support_request.md").write_text(ticket)
    print(f"\nSupport ticket drafted -> {out / 'support_request.md'}")
    print("Send it via perplexity.ai  >  Help  >  Contact Support, and attach credits.json.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
