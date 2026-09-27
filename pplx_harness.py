"""Sandbox port of pplx_connector / pplx_ui for the Perplexity Computer browser harness.

Why this exists
  Playwright Chromium launched inside the Computer sandbox is stopped by
  Cloudflare's bot check on perplexity.ai. The harness's cloud browser passes it,
  so this module re-implements the same text/role-based flow on top of the
  harness helpers (accessibility_tree, click_by_ref, fill_input, ...). No Keychain,
  no cookies: sign-in is Perplexity's own emailed one-time code, relayed by you.

How to run (from a browser_exec snippet; helpers are already bound)
  exec(open("/home/user/workspace/pplx-billing-helper/pplx_harness.py").read())
  H = Harness(globals())
  H.open("connectors")
  print(H.login_start("you@example.com"))      # -> "code_sent" | "signed_in" | "unknown"
  # ...user relays the 6-digit code...
  print(H.login_code("123456"))
  print(H.connectors_list())
  print(H.connector_add("Filesystem Bridge", "https://host/mcp", auth="api-key",
                        transport="http", api_key="...", description="..."))
  # review the screenshot the call saved, get approval, then:
  print(H.submit())                              # clicks Add

Every action returns a dict with what it clicked or filled and what it missed,
so the caller can degrade to manual steps. Nothing presses Add except submit().
"""
from __future__ import annotations

import re
import time

BASE = "https://www.perplexity.ai"
PAGES = {
    "home": f"{BASE}/",
    "usage": f"{BASE}/account/usage",
    "account": f"{BASE}/settings/account",
    "connectors": f"{BASE}/account/connectors",
    "api": f"{BASE}/settings/api",
}
AUTH_LABEL = {"api-key": r"api ?key", "oauth": r"oauth", "none": r"^none$|no auth"}
TRANSPORT_LABEL = {"http": r"streamable ?http", "sse": r"^sse$|server[- ]sent"}

# Axtree lines look like:  - button "Continue with email" [ref=ref_1568] disabled ...
_LINE = re.compile(r'^\s*[-+~]?\s*(?P<role>[A-Za-z]+)\s+"(?P<name>(?:[^"\\]|\\.)*)"\s+\[ref=(?P<ref>ref_\d+)\](?P<rest>.*)$')


class Harness:
    def __init__(self, g: dict, shots_dir: str = "/home/user/workspace/pplx-billing-helper/shots"):
        self.g = g            # browser_exec globals: helpers are looked up here
        self.shots_dir = shots_dir
        self.log: list[str] = []

    # ---------- plumbing ----------
    def _h(self, name):
        return self.g[name]

    def _say(self, msg: str) -> None:
        self.log.append(msg)

    def tree(self, interactive: bool = True, full: bool = True) -> str:
        return self._h("accessibility_tree")(filter_type="interactive" if interactive else "all", full=full)

    def nodes(self, tree: str | None = None) -> list[dict]:
        out = []
        for line in (tree or self.tree()).splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            d = m.groupdict()
            d["disabled"] = " disabled" in d["rest"]
            d["checked"] = 'checked="true"' in d["rest"] or " checked" in d["rest"]
            out.append(d)
        return out

    def find(self, name_patterns: list[str], roles: tuple[str, ...] | None = None,
             tree: str | None = None) -> dict | None:
        ns = self.nodes(tree)
        for pat in name_patterns:
            rx = re.compile(pat, re.I)
            for n in ns:
                if roles and n["role"].lower() not in roles:
                    continue
                if rx.search(n["name"]):
                    return n
        return None

    def shot(self, label: str) -> str:
        import os
        os.makedirs(self.shots_dir, exist_ok=True)
        path = f"{self.shots_dir}/{int(time.time())}-{label}.jpg"
        return self._h("capture_screenshot")(path=path)

    # ---------- primitives (mirror pplx_ui) ----------
    def click_first(self, patterns: list[str], roles=("button", "link", "menuitem", "tab", "option", "radio")) -> bool:
        n = self.find(patterns, roles)
        if not n or n["disabled"]:
            return False
        self._h("click_by_ref")(n["ref"])
        self._say(f"clicked {n['role']} '{n['name']}'")
        return True

    def click_text(self, patterns: list[str]) -> bool:
        n = self.find(patterns, None, self.tree(interactive=False))
        if not n:
            return False
        self._h("click_by_ref")(n["ref"])
        self._say(f"clicked {n['role']} '{n['name']}'")
        return True

    def fill_first(self, patterns: list[str], value: str, secret: bool = False) -> bool:
        n = self.find(patterns, ("textbox", "searchbox", "combobox"))
        if not n:
            return False
        self._h("click_by_ref")(n["ref"])
        self._h("press_key")("cmd+a")
        self._h("type_text")(value)
        self._say(f"filled '{n['name']}' = {'********' if secret else value}")
        return True

    def choose(self, field_patterns: list[str], option_patterns: list[str]) -> bool:
        so = self._h("select_option")
        # 1. harness select_option handles native selects and custom dropdowns by label.
        for fpat in field_patterns:
            n = self.find([fpat], ("combobox", "listbox", "button"))
            if n:
                for opat in option_patterns:
                    try:
                        so(n["ref"], self._plain(opat))
                        self._say(f"selected {opat} in '{n['name']}'")
                        return True
                    except Exception:
                        continue
        # 2. Radio / segmented control labelled with the option text itself.
        if self.click_first(option_patterns, ("radio", "tab", "option", "button")):
            return True
        # 3. Open trigger, then click option.
        if self.click_first(field_patterns, ("combobox", "button")):
            if self.click_first(option_patterns, ("option", "menuitem", "button")):
                return True
            return self.click_text(option_patterns)
        return False

    @staticmethod
    def _plain(pattern: str) -> str:
        """Best-effort visible text from a regex: 'streamable ?http' -> 'Streamable HTTP'."""
        t = re.sub(r"[\^$?()|\\]", "", pattern).split("|")[0].replace(" ?", " ").strip()
        return {"api key": "API Key", "oauth": "OAuth", "none": "None",
                "streamable http": "Streamable HTTP", "sse": "SSE"}.get(t.lower(), t)

    def check_first(self, patterns: list[str]) -> bool:
        n = self.find(patterns, ("checkbox", "switch"))
        if not n:
            return False
        if not n["checked"]:
            self._h("click_by_ref")(n["ref"])
        self._say(f"checked '{n['name']}'")
        return True

    # ---------- session ----------
    def open(self, target: str) -> dict:
        url = PAGES.get(target) or (target if target.startswith("http") else BASE + "/" + target.lstrip("/"))
        if not self._h("current_tab")():
            self._h("ensure_real_tab")() or self._h("create_tab")("about:blank")
        self._h("goto_url")(url)
        info = self.wait_for_challenge()
        if self.challenged(info):
            return {"url": info["url"], "title": info["title"], "signed_in": False, "challenged": True}
        self.dismiss_cookie_banner()
        return {"url": info["url"], "title": info["title"], "signed_in": self.signed_in(), "challenged": False}

    @staticmethod
    def challenged(info: dict) -> bool:
        return bool(re.search(r"just a moment|checking your browser|security verification", info.get("title", ""), re.I))

    def wait_for_challenge(self, timeout_s: float = 30.0) -> dict:
        """Cloudflare's interstitial usually clears itself within a few seconds; poll it."""
        t0 = time.time()
        info = self._h("page_info")()
        while self.challenged(info) and time.time() - t0 < timeout_s:
            self._h("wait")(2)
            info = self._h("page_info")()
        if self.challenged(info):
            self._say("cloudflare challenge did not clear")
        return info

    def dismiss_cookie_banner(self) -> None:
        self.click_first([r"only necessary", r"reject", r"decline"])

    def signed_in(self) -> bool:
        if self.challenged(self._h("page_info")()):
            return False
        t = self.tree()
        return self.find([r"continue with (google|apple|email)", r"^sign in$", r"^log in$"], ("button",), t) is None \
            and self.find([r"enter your email"], ("textbox",), t) is None

    def login_start(self, email: str) -> str:
        """Type the email and request a one-time code. Returns code_sent | signed_in | unknown."""
        if self.signed_in():
            return "signed_in"
        if not self.fill_first([r"enter your email", r"^email"], email):
            return "unknown"
        if not self.click_first([r"continue with email", r"^continue$"]):
            return "unknown"
        t = self.tree()
        if self.find([r"code", r"otp", r"digit"], ("textbox",), t) or self.find([r"check your (email|inbox)", r"we (sent|emailed)"], None, t):
            self.shot("login-code-prompt")
            return "code_sent"
        return "unknown"

    def login_code(self, code: str) -> str:
        box = self.find([r"code", r"otp", r"digit", r"^$"], ("textbox",))
        if not box:
            return "no_code_box"
        self._h("click_by_ref")(box["ref"])
        self._h("type_text")(code)
        if not self.click_first([r"continue", r"verify", r"submit", r"sign in"]):
            self._h("press_key")("Enter")
        self._h("wait_for_load")(15)
        return "signed_in" if self.signed_in() else "not_signed_in"

    # ---------- connectors ----------
    def connectors_list(self) -> dict:
        self.open("connectors")
        t = self.tree(interactive=False)
        heads = [n["name"] for n in self.nodes(t) if n["role"].lower() in ("heading",) and 0 < len(n["name"]) < 60]
        return {"headings": heads, "shot": self.shot("connectors")}

    def connector_add(self, name: str, url: str, auth: str = "api-key", transport: str = "http",
                      api_key: str | None = None, description: str = "") -> dict:
        if not url.lower().startswith("https://"):
            raise ValueError("MCP Server URL must be https://")
        if auth == "api-key" and not api_key:
            raise ValueError("api_key required for auth='api-key'")
        self.open("connectors")
        if not self.click_first([r"\+?\s*custom connector", r"add (custom )?connector"]):
            return {"ok": False, "stage": "open-form", "shot": self.shot("no-custom-connector"), "log": self.log}
        self.click_first([r"^remote$"], ("button", "tab", "radio", "option", "menuitem")) or self.click_text([r"^remote$"])

        done = {}
        done["name"] = self.fill_first([r"^name$", r"display name", r"connector name"], name)
        done["url"] = self.fill_first([r"mcp server url", r"server url", r"^url$"], url)
        if description:
            done["description"] = self.fill_first([r"description"], description)
        done["auth"] = self.choose([r"authentication", r"^auth"], [AUTH_LABEL[auth]])
        if api_key:
            done["api_key"] = self.fill_first([r"api ?key", r"token", r"secret"], api_key, secret=True)
        done["transport"] = self.choose([r"transport"], [TRANSPORT_LABEL[transport]])
        done["ack"] = self.check_first([r"understand", r"acknowledge", r"risk"])
        add = self.find([r"^add$", r"^create$", r"^save$"], ("button",))
        missed = [k for k, v in done.items() if not v]
        return {"ok": not missed, "filled": done, "missed": missed,
                "add_button": (add["name"], not add["disabled"]) if add else None,
                "shot": self.shot("connector-form"), "log": self.log}

    def submit(self) -> dict:
        """Press Add. Call only after the user approved the filled form."""
        ok = self.click_first([r"^add$", r"^create$", r"^save$"])
        self._h("wait_for_load")(15)
        return {"clicked": ok, "shot": self.shot("after-add"), "log": self.log}
