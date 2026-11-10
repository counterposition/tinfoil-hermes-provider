"""What Tinfoil's own model catalog says about each model.

``GET /v1/models`` rows carry ``type``, ``tool_calling``, ``deprecated``,
``context_window``, ``multimodal``, ``reasoning`` and ``supported_efforts``.
The profile remembers them so a model Tinfoil ships after this plugin was
released still gets the right picker entry, context window and reasoning
levels. The last catalog seen is kept on disk because the hooks that need it
on the request path may not do I/O.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLUGIN_NAME = "tinfoil-provider"
_SNAPSHOT_NAME = "catalog.json"
_FIELDS = (
    "type",
    "tool_calling",
    "deprecated",
    "context_window",
    "multimodal",
    "reasoning",
    "supported_efforts",
)

_lock = threading.Lock()
_rows: dict[str, dict[str, Any]] = {}
_warmed_homes: set[str] = set()


def bare(model: str | None) -> str:
    """``tinfoil/Kimi-K3`` and ``kimi-k3`` name the same catalog row."""
    return (model or "").strip().lower().rsplit("/", 1)[-1]


def _snapshot_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "plugin-data" / PLUGIN_NAME / _SNAPSHOT_NAME


def _normalize(items: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        model_id = bare(str(item.get("id") or ""))
        if model_id:
            rows[model_id] = {key: item[key] for key in _FIELDS if key in item}
    return rows


def serves_agents(row: dict[str, Any]) -> bool:
    """Whether a catalog row is a chat model an agent can drive.

    A row that says nothing about itself is kept: an older proxy, or a gateway
    in front of it, may return bare ids.
    """
    if row.get("deprecated") is True or row.get("tool_calling") is False:
        return False
    return str(row.get("type") or "chat").strip().lower() == "chat"


def agent_model_ids(items: Any) -> list[str]:
    """Catalog ids in catalog order, without embeddings, audio, safety and retired models."""
    ids: list[str] = []
    for item in items if isinstance(items, list) else ():
        model_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
        if model_id and model_id not in ids and serves_agents(item):
            ids.append(model_id)
    return ids


def remember(items: Any) -> None:
    """Replace what is known with a freshly fetched catalog and save it for the next process."""
    rows = _normalize(items)
    if not rows:
        return
    with _lock:
        _rows.clear()
        _rows.update(rows)
    try:
        from plugins.plugin_storage import plugin_data_dir
        from utils import atomic_json_write

        atomic_json_write(plugin_data_dir(PLUGIN_NAME) / _SNAPSHOT_NAME, rows)
    except Exception:
        logger.debug("Could not save the Tinfoil catalog snapshot", exc_info=True)


def warm() -> None:
    """Load the saved catalog once per Hermes home. Call only where I/O is allowed."""
    try:
        path = _snapshot_path()
    except Exception:
        return
    with _lock:
        if str(path) in _warmed_homes:
            return
        _warmed_homes.add(str(path))
        have_rows = bool(_rows)
    if have_rows:
        return
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(rows, dict):
        with _lock:
            for model_id, row in rows.items():
                if isinstance(row, dict):
                    _rows.setdefault(bare(model_id), row)


def row(model: str | None) -> dict[str, Any] | None:
    with _lock:
        return _rows.get(bare(model))


def efforts(model: str | None) -> tuple[str, ...] | None:
    """Reasoning levels the catalog lists for *model*: ``()`` is none, ``None`` is unknown."""
    found = row(model)
    if found is None:
        return None
    levels = found.get("supported_efforts")
    if isinstance(levels, list):
        return tuple(str(level).strip().lower() for level in levels if str(level).strip())
    return () if found.get("reasoning") is False else None


def context_window(model: str | None) -> int | None:
    found = row(model)
    window = found.get("context_window") if found else None
    return window if isinstance(window, int) and not isinstance(window, bool) and window > 0 else None


def capabilities() -> dict[str, dict[str, Any]]:
    """Known chat models in the shape of Hermes' ``model_overrides``; absent facts stay absent."""
    declared: dict[str, dict[str, Any]] = {}
    with _lock:
        rows = dict(_rows)
    for model_id, found in rows.items():
        if not serves_agents(found):
            continue
        entry: dict[str, Any] = {}
        for source, target in (
            ("reasoning", "supports_reasoning"),
            ("multimodal", "supports_vision"),
            ("tool_calling", "supports_tools"),
        ):
            if isinstance(found.get(source), bool):
                entry[target] = found[source]
        window = context_window(model_id)
        if window:
            entry["context_window"] = window
        if entry:
            declared[model_id] = entry
    return declared
