# pplx-billing-helper

Drive Perplexity's account UI locally, in your own Chrome, with your own login,
for the chores that have no API and that a hosted agent can't safely do for you.
Secrets (card digits, connector API keys) live in macOS Keychain and never pass
through chat, a tunnel, or a third-party service. The script fills forms; you
click the final button.

| File | What it drives |
|---|---|
| `pplx_ui.py` | Shared core: persistent Chrome profile, text/role-based locators, Keychain, hand-off. Also a tiny CLI: `login`, `open <page>`, `shot <page>`. |
| `pplx_billing.py` | Billing: change card, buy credits, auto-refill. |
| `pplx_connector.py` | Account settings → Connectors: list, and fill the "+ Custom connector → Remote" form. |
| `check_credits.py` | Read-only credit balance from the terminal (no browser). |
| `pplx_harness.py` | Same connector flow, ported to the Perplexity Computer browser harness so it runs from a Computer sandbox. |

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
| Open any settings page and hand off | `python pplx_ui.py open connectors` (also `usage`, `account`, `api`, a `/path`, or a URL) |
| Screenshot a page | `python pplx_ui.py shot usage --out usage.png` |
| List connectors | `python pplx_connector.py list` |
| Add a remote MCP connector | `python pplx_connector.py add --name "Filesystem Bridge" --url https://host/mcp --auth api-key` |
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
`PPLX_HANDOFF_MINUTES` (default 10) controls how long the window stays open.

## Adding a custom connector

`pplx_connector.py add` follows Perplexity's documented path (Account settings →
Connectors → **+ Custom connector** → **Remote**): it fills Name, MCP Server URL
(https required), optional Description, picks Authentication (`--auth api-key |
oauth | none`) and Transport (`--transport http | sse`), ticks the risk
acknowledgement, optionally saves a screenshot (`--shot form.png`), and stops.
You click **Add**, then click the new card to authenticate and enable it.

Store the API key once, in Terminal:

```sh
security add-generic-password -a pplx -s pplx-connector-key -U -w '<your key>'
```

Or pipe it: `echo "$KEY" | python pplx_connector.py add ... --api-key-stdin`.
Use `--keychain <service>` to keep one item per connector.

Pair it with a local MCP server plus tunnel (e.g. `cloudflared tunnel --url
http://localhost:8000`) to register a Filesystem Bridge without pasting the key
into chat. Quick `trycloudflare.com` URLs change on every restart; edit the
connector via its **⋮** menu when that happens.

## Running from a Perplexity Computer sandbox

Playwright Chromium launched inside the sandbox is stopped by Cloudflare's bot
check on perplexity.ai, but the harness's cloud browser passes it. `pplx_harness.py`
re-implements the connector flow on the harness helpers (accessibility tree +
refs) so the agent can run it directly:

```python
exec(open("/home/user/workspace/pplx-billing-helper/pplx_harness.py").read())
H = Harness(globals())
H.open("connectors")
H.login_start("you@example.com")   # Perplexity emails a one-time code
H.login_code("123456")             # you relay the code
H.connector_add("Filesystem Bridge", "https://host/mcp", auth="api-key",
                transport="http", api_key="...")   # fills, screenshots, stops
H.submit()                         # only after you approve the screenshot
```

Trade-offs versus the Mac scripts: no Keychain (the API key is typed by the
agent, so use a key you generated for this purpose), the session lives only in
the throwaway cloud browser, and the agent presses Add on your explicit go-ahead
instead of you clicking it.

## Build your own command

```python
from pplx_ui import browser, ensure_logged_in, goto, click_first, fill_first, handoff

with browser() as page:
    ensure_logged_in(page)
    goto(page, "api")                      # page name, /path, or URL
    click_first(page, [r"generate", r"new key"])
    handoff(page, "Review, then confirm yourself.")
```

Helpers: `click_first`, `click_text`, `fill_first(..., secret=True)`, `choose`
(select / radio / custom dropdown), `check_first`, `screenshot`,
`keychain_secret(service, as_json=False)`. Every helper returns `False` instead
of raising when a locator misses, so commands can degrade to a hand-off.

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
  an issue noting the button text you saw. The connector form locators follow
  the Help Center article and have not been verified against every layout.
- Never do the subscription card change via the App Store / Google Play; that
  creates a second subscription. Credit packs via the mobile apps are fine.
- Cron scheduling `check_credits.py` is safe (read-only). Do not cron `buy`;
  use Perplexity's auto-refill for unattended purchases.
