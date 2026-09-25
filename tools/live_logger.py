"""JSONL logger for Gemini Live session events.

Captures exactly the fields the extended-thinking migration cares about:
interaction_status transitions, turn_complete boundaries, tool call/response
timing (start -> sent, per fc.id), and any exception raised while draining
the receive loop. Written as newline-delimited JSON so it can be tailed or
grepped during a live run, and diffed against a baseline for regressions.

Usage from live_cli.py:

    from tools.live_logger import LiveLogger
    logger = LiveLogger(path=".priya/live-events.jsonl")
    logger.log("status", status=status, turn_complete=turn_complete,
               saw_tool_call=saw_tool_call)
    logger.tool_start(tool_event_id, fc.name, args_dict)
    logger.tool_end(tool_event_id, fc.name, result)
    logger.error("receive_text", exc)

Or standalone, wrapping any async iterator of server messages:

    async for sc in logger.wrap(session.receive()):
        ...
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field


@dataclass
class LiveLogger:
    path: str = ".priya/live-events.jsonl"
    echo_stderr: bool = True
    _open_tools: dict = field(default_factory=dict)  # tool_event_id -> {name, t0}

    def __post_init__(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._fh = open(self.path, "a", buffering=1)

    def _write(self, record: dict):
        record.setdefault("t", time.time())
        line = json.dumps(record, default=str)
        self._fh.write(line + "\n")
        if self.echo_stderr:
            print(f"[live-log] {line}", file=sys.stderr)

    def log(self, kind: str, **fields):
        self._write({"kind": kind, **fields})

    def status(self, status, turn_complete, saw_tool_call, interrupted=None):
        self._write({
            "kind": "status",
            "interaction_status": status,
            "turn_complete": turn_complete,
            "saw_tool_call": saw_tool_call,
            "interrupted": interrupted,
        })

    def tool_start(self, tool_event_id, name, args):
        self._open_tools[tool_event_id] = {"name": name, "t0": time.time()}
        self._write({
            "kind": "tool_start", "id": tool_event_id, "name": name,
            "args": args,
        })

    def tool_end(self, tool_event_id, name, result):
        opened = self._open_tools.pop(tool_event_id, None)
        latency_ms = None
        if opened is not None:
            latency_ms = round((time.time() - opened["t0"]) * 1000, 1)
        is_error = isinstance(result, dict) and "error" in result
        self._write({
            "kind": "tool_end", "id": tool_event_id, "name": name,
            "latency_ms": latency_ms, "is_error": is_error,
            "result_preview": _preview(result),
        })

    def tool_response_sent(self, tool_event_id, name):
        # Call right after _send_tool_response returns for this fc, so a
        # batched-vs-per-call regression shows up as a timing gap between
        # tool_end and this event for every id in the same turn.
        self._write({"kind": "tool_response_sent", "id": tool_event_id, "name": name})

    def stall(self, where: str, detail: str = ""):
        self._write({"kind": "stall", "where": where, "detail": detail})

    def error(self, where: str, exc: BaseException):
        self._write({
            "kind": "error", "where": where,
            "exc_type": type(exc).__name__, "exc": str(exc),
            "traceback": traceback.format_exc(),
        })

    def hard_error_check(self, exc: BaseException) -> str | None:
        """Classify known extended-thinking connect-time errors.

        Returns a short diagnosis string, or None if unrecognized.
        """
        msg = str(exc)
        if "BLOCKING" in msg and ("not supported" in msg.lower() or "invalid" in msg.lower()):
            return ("A tool declares behavior=BLOCKING. "
                    "gemini-3.8-live-extended-thinking only accepts NON_BLOCKING "
                    "and hard-errors on connect otherwise.")
        if "thinking_level" in msg.lower() and "minimal" in msg.lower():
            return "thinking_level=MINIMAL is not supported on extended-thinking; use low/medium/high."
        if "scheduling" in msg.lower() and "unsupported" in msg.lower():
            return "Function scheduling configs are not supported on extended-thinking."
        return None

    async def wrap(self, aiter):
        """Wrap an async iterator (e.g. session.receive()), logging each
        interaction_status/turn_complete and any exception, without changing
        the values it yields."""
        try:
            async for item in aiter:
                sc = getattr(item, "server_content", None)
                if sc is not None:
                    self.status(
                        status=getattr(sc, "interaction_status", None),
                        turn_complete=bool(getattr(sc, "turn_complete", False)),
                        saw_tool_call=bool(getattr(item, "tool_call", None)),
                        interrupted=getattr(sc, "interrupted", None),
                    )
                yield item
        except Exception as exc:
            self.error("receive_stream", exc)
            raise

    def close(self):
        self._fh.close()


def _preview(result, limit=300):
    try:
        s = json.dumps(result, default=str)
    except Exception:
        s = str(result)
    return s if len(s) <= limit else s[:limit] + "…"
