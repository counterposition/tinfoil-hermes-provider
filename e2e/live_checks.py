"""Live checks against the attested proxy (2026-08-23 plan §7, items 2-5).

Needs TINFOIL_API_KEY in $HERMES_HOME/.env and a reachable proxy. Uses the
profile's own request translation so the effort clamps are exercised against
the real API, and drives a three-turn tool-calling exchange per model family
with the OpenAI SDK through the proxy.
"""

from __future__ import annotations

import sys

MODELS = ("kimi-k3-1-pro", "glm-5-3", "gpt-oss-120b", "deepseek-v4-1-flash")
TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}


def main() -> int:
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from openai import OpenAI
    from providers import get_provider_profile

    profile = get_provider_profile("tinfoil")
    failures = 0

    # §7.2 resolution evidence: the provider actually resolved to the proxy URL.
    runtime = resolve_runtime_provider(requested="tinfoil", target_model=MODELS[0])
    print("resolved:", {k: runtime.get(k) for k in ("provider", "api_mode", "base_url")})
    if runtime.get("base_url") != profile.base_url:
        print("FAIL resolution: base_url is not the proxy")
        failures += 1
    client = OpenAI(api_key=runtime["api_key"], base_url=runtime["base_url"])

    # §7.3 context length from the proxy catalog.
    catalog = {m.id: m for m in client.models.list().data}
    experimental = "deepseek-v4-1-flash"
    ctx = getattr(catalog.get(experimental), "context_window", None)
    print(f"{experimental} context_window:", ctx)

    # §7.4 multi-turn tool calling per family, with §7.5 effort clamps (xhigh).
    for model in MODELS:
        if model == experimental and model not in catalog:
            print(f"SKIP {model}: experimental model absent from the catalog")
            continue
        _, extras = profile.build_api_kwargs_extras(reasoning_config={"effort": "xhigh"}, model=model)
        messages = [
            {"role": "system", "content": "Use the read_file tool when asked about a file. Be brief."},
            {"role": "user", "content": "What is in /etc/hostname? Use the tool."},
        ]
        try:
            first = client.chat.completions.create(model=model, messages=messages, tools=[TOOL], **extras)
            msg = first.choices[0].message
            reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
            calls = msg.tool_calls or []
            if not calls:
                print(f"FAIL {model}: no tool call in turn 1 (content={msg.content!r})")
                failures += 1
                continue
            messages.append(msg.model_dump(exclude_none=True))
            for call in calls:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": "e2e-container\n"})
            second = client.chat.completions.create(model=model, messages=messages, tools=[TOOL], **extras)
            answer = second.choices[0].message
            messages.append(answer.model_dump(exclude_none=True))
            messages.append({"role": "user", "content": "Reply with the single word OK."})
            third = client.chat.completions.create(model=model, messages=messages, tools=[TOOL], **extras)
            final = third.choices[0].message.content or ""
            ok = "e2e-container" in (answer.content or "") and "OK" in final.upper()
            print(f"{'PASS' if ok else 'FAIL'} {model}: effort={extras}, reasoning_field={'yes' if reasoning else 'no'}, "
                  f"turn2={answer.content!r:.80}, turn3={final!r:.40}")
            if not ok:
                failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {model}: {type(exc).__name__}: {str(exc)[:300]}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
