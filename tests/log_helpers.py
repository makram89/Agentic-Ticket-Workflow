"""Capture log records formatted by the production JsonFormatter on the root logger."""
import io
import json
import logging
from contextlib import contextmanager

IGNORED_LOGGERS = ("httpx", "httpcore", "asyncio")


class Captured:
    def __init__(self, stream):
        self.stream = stream

    def lines(self):
        out = []
        for raw in self.stream.getvalue().splitlines():
            if raw.strip():
                out.append(raw)
        return out

    def records(self):
        """Every captured line parsed as JSON (fails the test if a line is not JSON)."""
        recs = []
        for raw in self.lines():
            try:
                recs.append(json.loads(raw))
            except ValueError as exc:  # pragma: no cover - assertion message
                raise AssertionError(f"log line is not JSON: {raw!r}") from exc
        return recs

    def ticket_records(self, ticket_id):
        """Lines that are about the ticket (mention its id anywhere), excluding HTTP client noise."""
        return [
            r
            for r in self.records()
            if ticket_id in json.dumps(r) and not str(r.get("logger", "")).startswith(IGNORED_LOGGERS)
        ]

    def mark(self):
        return len(self.lines())


@contextmanager
def capture_json_logs(level=logging.INFO):
    from ticketflow.observability.logging import JsonFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(level)
    try:
        yield Captured(stream)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def assert_ticket_line(rec, ticket_id, workflow_id):
    for key in ("timestamp", "level", "logger", "message"):
        assert key in rec, f"missing {key!r} in {rec}"
    assert rec.get("ticket_id") == ticket_id, rec
    assert rec.get("workflow_id") == workflow_id, rec
