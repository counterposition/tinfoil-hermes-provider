"""Drive the interactive ``hermes model`` picker through a pty.

Walks the curses provider list until Tinfoil is highlighted, selects it,
answers the API-key and Base URL prompts, confirms the live model list is
shown, and picks the first model. Uses a terminal emulator (pyte) so the
curses screen can be read as text; the final screen is printed on failure.
"""

from __future__ import annotations

import os
import sys
import time

import pexpect
import pyte

ROWS, COLS = 50, 160
KEY = "e2e-placeholder-key"
DOWN = "\x1b[B"


class Terminal:
    def __init__(self, child: pexpect.spawn):
        self.child = child
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.Stream(self.screen)

    def pump(self, quiet: float = 0.4, limit: float = 30.0) -> None:
        """Feed output until the child has been quiet for ``quiet`` seconds."""
        deadline = time.time() + limit
        last = time.time()
        while time.time() < deadline:
            try:
                data = self.child.read_nonblocking(65536, timeout=0.1)
                self.stream.feed(data)
                last = time.time()
            except pexpect.TIMEOUT:
                if time.time() - last >= quiet:
                    return
            except pexpect.EOF:
                return

    def lines(self) -> list[str]:
        return [line.rstrip() for line in self.screen.display]

    def text(self) -> str:
        return "\n".join(self.lines())

    def wait_for(self, needle: str, limit: float = 45.0) -> bool:
        deadline = time.time() + limit
        while time.time() < deadline:
            self.pump(quiet=0.3, limit=2.0)
            if needle in self.text():
                return True
        return False

    def dump(self, tag: str) -> None:
        print(f"--- screen: {tag} ---")
        for line in self.lines():
            if line:
                print("  | " + line)
        print("--- end ---")


def cursor_line(term: Terminal) -> str:
    """The row the curses cursor (→) is on; (●) is the radio state, not the cursor."""
    return next((line for line in term.lines() if line.lstrip().startswith("→")), "")


def main() -> int:
    env = dict(os.environ, TERM="xterm-256color", LINES=str(ROWS), COLUMNS=str(COLS))
    child = pexpect.spawn("hermes", ["model"], env=env, encoding="utf-8", timeout=60,
                          dimensions=(ROWS, COLS))
    term = Terminal(child)
    try:
        if not term.wait_for("Select provider"):
            term.dump("no provider picker")
            return 1
        if "Tinfoil" not in term.text():
            term.dump("provider list without Tinfoil")
            return 1
        print("provider list shows Tinfoil")

        for _ in range(200):
            selected = cursor_line(term)
            if "Tinfoil" in selected:
                break
            child.send(DOWN)
            term.pump(quiet=0.15, limit=3.0)
        else:
            term.dump("could not highlight Tinfoil")
            return 1
        print("highlighted:", selected.strip())
        child.send("\r")

        deadline = time.time() + 45
        while time.time() < deadline:
            term.pump(quiet=0.3, limit=2.0)
            text = term.text()
            if "[K]eep / [R]eplace / [C]lear" in text:
                print("a TINFOIL_API_KEY is already configured; keeping it")
                child.sendline("")
                break
            if "TINFOIL_API_KEY" in text:
                print("prompted for TINFOIL_API_KEY")
                child.sendline(KEY)
                break
        else:
            term.dump("no API key prompt")
            return 1

        if not term.wait_for("Base URL ["):
            term.dump("no Base URL prompt")
            return 1
        prompt_line = next(line for line in term.lines() if "Base URL [" in line)
        default_base = prompt_line.split("Base URL [", 1)[1].split("]", 1)[0]
        print(f"prompted for Base URL, default {default_base!r}")
        if not default_base.startswith("http://127.0.0.1:3301"):
            return 1
        child.sendline("")

        if not term.wait_for("kimi-k3", limit=60):
            term.dump("model picker without kimi-k3")
            return 1
        text = term.text()
        seen = [m for m in ("kimi-k3", "glm-5-3", "deepseek-v4-flash", "gpt-oss-120b", "whisper-large-v3-turbo") if m in text]
        print("model picker shows:", seen)
        rows = [line.split(")", 1)[1].strip() for line in term.lines() if "(○)" in line or "(●)" in line]
        from providers import get_provider_profile

        curated_first = get_provider_profile("tinfoil").fallback_models[0]
        if rows and rows[0] == curated_first:
            print("OBSERVED  hermes model lists curated fallbacks first")
        else:
            print(f"OBSERVED  hermes model lists the live catalog in the proxy's order (first row {rows[0] if rows else None!r}); "
                  "the setup flow's generic branch uses fetch_api_models and ignores fallback_models (upstream behavior)")
        selected = cursor_line(term)
        print("highlighted:", selected.strip())
        child.send("\r")
        term.pump(quiet=1.0, limit=60.0)
        term.dump("after selection")
    finally:
        if child.isalive():
            child.terminate(force=True)

    import yaml

    cfg = yaml.safe_load(open(os.path.join(os.environ["HERMES_HOME"], "config.yaml")))
    model = cfg.get("model")
    print("config.yaml model section:", model)
    if isinstance(model, dict) and model.get("provider") == "tinfoil" and model.get("default"):
        return 0
    print("config.yaml does not record provider tinfoil with a default model")
    return 1


if __name__ == "__main__":
    sys.exit(main())
