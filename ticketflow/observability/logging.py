"""JSON-line logging (R17, R26)."""
import json
import logging
import sys
from datetime import datetime, timezone

from opentelemetry import trace

# Attributes every LogRecord has; anything else on a record came in through `extra=`.
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """timestamp, level, logger, message + extras at top level.

    Temporal's `temporal_workflow` / `temporal_activity` extras are flattened into workflow_id
    (+ run_id, activity_type); trace_id / span_id are added when a valid span is current.
    """

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if key == "temporal_workflow" and isinstance(value, dict):
                out.setdefault("workflow_id", value.get("workflow_id"))
                out.setdefault("run_id", value.get("run_id"))
            elif key == "temporal_activity" and isinstance(value, dict):
                out.setdefault("workflow_id", value.get("workflow_id"))
                out.setdefault("run_id", value.get("workflow_run_id"))
                out.setdefault("activity_type", value.get("activity_type"))
            else:
                out[key] = value
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            out["trace_id"] = format(ctx.trace_id, "032x")
            out["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            out["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
