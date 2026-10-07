"""Centralized structlog configuration.

- Dev mode  : human-readable, colored console output.
- Prod mode : machine-parseable JSON lines.

Also bridges stdlib logging (uvicorn, httpx, openai, chromadb) so all log
records flow through the same processor chain.

A secret scrubber redacts API keys and truncates long prompt/content
strings, so we never leak credentials or full LLM prompts to logs.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, List, Optional

import structlog

_configured: bool = False

# Context variable holding request-scoped fields. Bound via
# bind_request_context() and automatically merged into every log event.
# default=None (not {}) so we never share a mutable default across contexts.
_request_context: ContextVar = ContextVar("request_context", default=None)

# Keys whose values must be redacted before emission.
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "llm_api_key",
    "token",
    "access_token",
    "secret",
    "password",
}

# Fields truncated to N characters (full prompts overwhelm logs).
_TRUNCATE_KEYS = {"prompt", "content", "user_prompt", "system_prompt", "raw"}


def _merge_request_context(_, __, event_dict: dict) -> dict:
    ctx = _request_context.get()
    if ctx:
        # Do not clobber explicit fields passed at the call site.
        for k, v in ctx.items():
            event_dict.setdefault(k, v)
    return event_dict


def configure_logging(
    level: str = "INFO",
    fmt: str = "auto",
    debug: bool = False,
) -> None:
    """Idempotent structlog + stdlib logging bootstrap."""
    global _configured
    if _configured:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Resolve 'auto' -> console when in debug, json otherwise.
    resolved = fmt
    if resolved == "auto":
        resolved = "console" if debug else "json"

    # Resolve truncation limit for prompt-like fields.
    truncate_limit = 200
    try:
        from port_tariff.config import get_settings

        truncate_limit = int(get_settings().log_prompt_preview_chars)
    except Exception:
        pass

    def _secret_scrubber(_, __, event_dict: dict) -> dict:
        for key in list(event_dict.keys()):
            lower = key.lower()
            if lower in _SENSITIVE_KEYS:
                event_dict[key] = "***redacted***"
            elif lower in _TRUNCATE_KEYS and isinstance(event_dict[key], str):
                val = event_dict[key]
                if len(val) > truncate_limit:
                    event_dict[key] = (
                        val[:truncate_limit] + f"... (+{len(val) - truncate_limit} chars)"
                    )
        return event_dict

    shared_processors: List[Callable] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _merge_request_context,
        _secret_scrubber,
    ]

    if resolved == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    # Wire structlog itself: last step hands off to stdlib ProcessorFormatter.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging so uvicorn / httpx / openai / chromadb flow through.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)

    # Silence chatty third-party loggers.
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "chromadb", "asyncio"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    _configured = True


def get_logger(name: Optional[str] = None):
    return structlog.get_logger(name)


def bind_request_context(**fields: Any) -> None:
    """Bind request-scoped fields (request_id, port, vessel_name, ...).

    Values set here automatically appear on every subsequent log call in
    the same async context.
    """
    current = dict(_request_context.get() or {})
    current.update({k: v for k, v in fields.items() if v is not None})
    _request_context.set(current)


def clear_request_context() -> None:
    _request_context.set({})


@contextmanager
def timed(logger, event: str, **fields: Any) -> Iterator[dict]:
    """Time a block and emit ``event`` with ``elapsed_ms`` populated.

    Usage::

        with timed(log, "pipeline.parse.completed", path=p) as extra:
            ...do work...
            extra["chars"] = len(text)
    """
    start = time.perf_counter()
    extra: dict = {}
    try:
        yield extra
    finally:
        elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
        merged = {**fields, **extra}
        logger.info(event, elapsed_ms=elapsed_ms, **merged)


__all__ = [
    "configure_logging",
    "get_logger",
    "bind_request_context",
    "clear_request_context",
    "timed",
]
