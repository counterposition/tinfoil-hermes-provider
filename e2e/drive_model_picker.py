"""Drive the interactive ``hermes model`` picker through a pty.

Selects Tinfoil in the curses provider list, answers the API-key and Base URL
prompts, confirms the live model list is shown, and picks the first model.
Prints the transcript so a failure is diagnosable from the log.
"""

from __future__ import annotations

import os
import re
import sys

import pexpect

KEY = "e2e-placeholder-key"


def screen_text(child: pexpect.spawn) -> str:
    """Strip terminal escape sequences from what the child printed so far."""
    raw = child.before or ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]", "", raw)


def main() -> int:
    from hermes_cli.models import list_available_providers

    order = [p["id"] for p in list_available_providers()]
    idx = order.index("tinfoil")
    print(f"provider picker order: {order}\nTinfoil is entry #{idx}")

    env = dict(os.environ, TERM="xterm-256color", LINES="60", COLUMNS="120")
    child = pexpect.spawn("hermes", ["model"], env=env, encoding="utf-8", timeout=60,
                          dimensions=(60, 120))
    transcript: list[str] = []
    try:
        child.expect(["Tinfoil", pexpect.TIMEOUT], timeout=30)
        transcript.append(screen_text(child))
        if "Tinfoil" not in transcript[-1] + (child.after if isinstance(child.after, str) else ""):
            print("provider list never showed 'Tinfoil'")
            return 1
        print("provider list shows Tinfoil")
        for _ in range(idx):
            child.send("\x1b[B")  # Down arrow
        child.send("\r")
        child.expect("TINFOIL_API_KEY")
        transcript.append(screen_text(child))
        print("prompted for TINFOIL_API_KEY")
        child.sendline(KEY)
        child.expect(r"Base URL \[(.*?)\]:")
        default_base = child.match.group(1)
        print(f"prompted for Base URL, default {default_base!r}")
        if not default_base.startswith("http://127.0.0.1:3301"):
            print("Base URL default is not the local proxy")
            return 1
        child.sendline("")
        child.expect(["kimi-k3", pexpect.TIMEOUT], timeout=45)
        text = screen_text(child) + (child.after if isinstance(child.after, str) else "")
        transcript.append(text)
        live_seen = [m for m in ("kimi-k3", "glm-5-3", "deepseek-v4-flash", "gpt-oss-120b", "whisper-large-v3-turbo") if m in text]
        print("model picker showed:", live_seen)
        if "kimi-k3" not in live_seen:
            print("model picker did not show the curated first model")
            return 1
        child.send("\r")  # pick the highlighted (first) model
        child.expect(pexpect.EOF, timeout=60)
        transcript.append(screen_text(child))
    except pexpect.ExceptionPexpect as exc:
        print("pexpect failure:", type(exc).__name__)
        transcript.append(screen_text(child))
        print("\n--- transcript ---\n" + "\n".join(transcript) + "\n--- end ---")
        return 1
    finally:
        if child.isalive():
            child.terminate(force=True)

    import yaml

    cfg = yaml.safe_load(open(os.path.join(os.environ["HERMES_HOME"], "config.yaml")))
    model = cfg.get("model")
    print("config.yaml model section:", model)
    tail = transcript[-1].strip().splitlines()[-6:]
    print("\n".join("  | " + line for line in tail))
    if isinstance(model, dict) and model.get("provider") == "tinfoil" and model.get("default"):
        return 0
    print("config.yaml does not record provider tinfoil with a default model")
    return 1


if __name__ == "__main__":
    sys.exit(main())
