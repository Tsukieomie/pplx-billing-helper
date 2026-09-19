# pplx-billing-helper

Local workaround for the two Perplexity billing chores that have no API and that
a hosted agent can't safely do for you: swapping the card on file and buying
Computer credits. Everything runs on your Mac, in your own Chrome, with your own
login. Card digits live in macOS Keychain and never pass through chat, a tunnel,
or a third-party service.

There is no Perplexity write endpoint for payments — every open-source project
that touches Perplexity billing (CodexBar, TokenBar, usagebar, ceiling, tidemark,
Yomi, mission-control) is read-only against
`GET /rest/billing/credits`. `check_credits.py` reuses that; `pplx_billing.py`
drives the real web UI for the write side.

## Install

```sh
cd ~/pplx-billing-helper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chrome      # uses your installed Google Chrome
python pplx_billing.py login   # one-time sign-in, saved to ~/.pplx-billing-profile
```

## Use

| Goal | Command |
|---|---|
| See remaining credits | `python check_credits.py` (exit 3 when below `$PPLX_LOW_CREDITS`, default 500) |
| Change card (type it yourself) | `python pplx_billing.py card` |
| Change card, fill from Keychain | `python pplx_billing.py card --keychain` |
| Buy credits | `python pplx_billing.py buy --credits 5000` |
| Turn on auto-refill | `python pplx_billing.py refill` |

Store the card once, in Terminal (not in any chat):

```sh
security add-generic-password -a pplx -s pplx-billing-card -U \
  -w '{"number":"4242424242424242","exp":"12/30","cvc":"123","zip":"02903"}'
```

The script navigates and fills; it never presses Save, Add, or Pay. You review
and click the final button. Add `--pause` to get the Playwright Inspector.

## After the card is in

Turn on auto-refill at `perplexity.ai/account/usage` (or `python pplx_billing.py refill`).
That's Perplexity's own automation: it buys credits when you drop below 500 (Pro)
or 2,500 (Max), emails you each time, and respects a monthly cap you set. With
that on, you should never need `buy` again.

## Trigger from kenny-operator-mcp (optional)

If `KM_ALLOW_EXEC=1` is set on your bridge, `run_shell_command` can start the
helper from a Perplexity session on the Mac:

```
cd ~/pplx-billing-helper && .venv/bin/python pplx_billing.py card
```

The Chrome window still opens on your screen and you still click the final
button — the bridge only launches it. Keep the bridge on 127.0.0.1.

## Caveats

- Perplexity's account UI changes; locators are text-based and fall back to
  leaving the page open for you. If a step misses, finish it by hand and open
  an issue noting the button text you saw.
- Never do the subscription card change via the App Store / Google Play; that
  creates a second subscription. Credit packs via the mobile apps are fine.
- Cron scheduling `check_credits.py` is safe (read-only). Do not cron `buy`;
  use Perplexity's auto-refill for unattended purchases.
