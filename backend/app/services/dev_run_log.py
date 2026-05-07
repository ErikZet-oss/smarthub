"""In-memory log pre ladenie Playwright / košíka (Dev panel vo frontende)."""

from __future__ import annotations

import json
import os
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Any

_MAX = 8000
_lock = threading.Lock()
_buffer: deque[dict[str, Any]] = deque(maxlen=_MAX)
# None = riadiť sa SCRAPER_STEP_SCREENSHOTS; True/False = vynútené (Dev panel).
_step_screenshot_override: bool | None = None
_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_BASE_DIR = os.path.join(_DATA_DIR, "dev_screens")
os.makedirs(_BASE_DIR, exist_ok=True)
_DEV_LOG_FILE = os.path.join(_DATA_DIR, "dev_automation.ndjson")
_file_lock = threading.Lock()


def dev_screens_dir() -> str:
    return _BASE_DIR


def step_screenshots_enabled() -> bool:
    """Predvolene vypnuté; zapnúť cez SCRAPER_STEP_SCREENSHOTS=1 alebo Dev override."""
    with _lock:
        o = _step_screenshot_override
    if o is not None:
        return o
    v = os.environ.get("SCRAPER_STEP_SCREENSHOTS", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def set_step_screenshots_override(value: bool | None) -> None:
    """None = zrušiť override, použiť len env. True/False = vynútené pre tento proces."""
    global _step_screenshot_override
    with _lock:
        _step_screenshot_override = value


def get_step_screenshots_status() -> dict[str, Any]:
    with _lock:
        o = _step_screenshot_override
    v = os.environ.get("SCRAPER_STEP_SCREENSHOTS", "").strip().lower()
    env_on = v in ("1", "true", "yes", "on")
    effective = (o if o is not None else env_on)
    return {
        "effective": effective,
        "override": o,
        "env_enabled": env_on,
    }


def _append_dev_log_file(entry: dict[str, Any]) -> None:
    """Zrkadlí záznam na disk (pre tail / po páde procesu)."""
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    with _file_lock:
        try:
            with open(_DEV_LOG_FILE, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass


def dev_run_log(
    source: str,
    message: str,
    level: str = "info",
    **extra: Any,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "source": source,
        "message": message,
    }
    if extra:
        entry.update(extra)
    with _lock:
        _buffer.append(entry)
    _append_dev_log_file(entry)


def dev_run_log_exception(source: str, exc: BaseException) -> None:
    dev_run_log(source, f"{type(exc).__name__}: {exc}", "error")
    tb = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).strip()
    if tb:
        dev_run_log(source, tb, "trace")


def get_dev_logs(limit: int = 2000) -> list[dict[str, Any]]:
    with _lock:
        if limit <= 0:
            return []
        return list(_buffer)[-limit:]


def clear_dev_logs() -> None:
    with _lock:
        _buffer.clear()
