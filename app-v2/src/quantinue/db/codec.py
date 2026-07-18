"""Strict JSON codecs for reflected PostgreSQL boundaries."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from quantinue.core.contracts import PipelineContext

ContextPayload = dict[str, JsonValue]

CONTEXT_ADAPTER = TypeAdapter(PipelineContext)
_CONTEXT_PAYLOAD_ADAPTER = TypeAdapter(ContextPayload)


class AttemptRow(BaseModel):
    """Parsed shape of a reflected attempt row."""

    model_config = ConfigDict(strict=True)
    component: str
    attempt_no: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None


def encode_context(context: PipelineContext) -> ContextPayload:
    """Encode context without propagating untyped JSON."""
    return _CONTEXT_PAYLOAD_ADAPTER.validate_json(CONTEXT_ADAPTER.dump_json(context))
