"""tinfoil-sandbox: a Hermes terminal backend that runs commands in a Tinfoil Container.

Select it with ``terminal.backend: tinfoil`` and name the container in
``terminal.tinfoil_container``. Hermes' terminal, file and code tools then run
inside that enclave over the Tinfoil CLI's attested SSH tunnel. See README.md.
"""

from __future__ import annotations

import logging

from .provider import TinfoilSandboxProvider, container_home_paths

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    if not hasattr(ctx, "register_terminal_environment_provider"):
        logger.warning("tinfoil-sandbox: this Hermes has no terminal backend plugins; update Hermes.")
        return
    ctx.register_terminal_environment_provider(TinfoilSandboxProvider())
    ctx.register_hook("pre_tool_call", container_home_paths)
