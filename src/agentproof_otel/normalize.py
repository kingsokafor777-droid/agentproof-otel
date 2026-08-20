"""Deterministic, offline normalization from OTLP JSON to AgentProof Core traces."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from agentproof_core import (
    ActorKind,
    ActorRef,
    DataClassification,
    EventOutcome,
    SideEffect,
    ToolCall,
    ToolRef,
    WorkflowTrace,
)

_TRACE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{32}$")
_SPAN_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{16}$")
_IDENTIFIER_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9._:-]+")
_IDENTIFIER_EDGE_RE: Final[re.Pattern[str]] = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")
_NANOSECONDS_PER_SECOND: Final[int] = 1_000_000_000


class OtlpFormatError(ValueError):
    """Raised when untrusted OTLP JSON cannot be safely normalized."""


@dataclass(frozen=True)
class NormalizationConfig:
    """Explicit mapping controls for supported OTLP attributes."""

    tool_name_keys: tuple[str, ...] = ("gen_ai.tool.name", "agentproof.tool.name")
    actor_id_key: str = "agentproof.actor.id"
    actor_kind_key: str = "agentproof.actor.kind"
    side_effect_key: str = "agentproof.side_effect"
    requested_capabilities_key: str = "agentproof.requested_capabilities"
    input_labels_key: str = "agentproof.input_labels"
    output_labels_key: str = "agentproof.output_labels"


@dataclass(frozen=True)
class NormalizationDiagnostic:
    """A deterministic non-fatal observation about an otherwise valid source payload."""

    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """Serialize a diagnostic for CLI output."""

        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized Core traces and non-fatal diagnostics from one source input."""

    traces: tuple[WorkflowTrace, ...]
    diagnostics: tuple[NormalizationDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize traces with Core's JSON-mode representation."""

        return {
            "traces": [trace.model_dump(mode="json", exclude_none=True) for trace in self.traces],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True)
class _ObservedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    timestamp: datetime
    timestamp_nanos: int
    tool_name: str
    server_id: str
    tool_version: str | None
    actor: ActorRef
    outcome: EventOutcome
    side_effect: SideEffect
    requested_capabilities: tuple[str, ...]
    input_labels: tuple[DataClassification, ...]
    output_labels: tuple[DataClassification, ...]
    service_name: str
    scope_name: str
    status_code: int


def normalize_otlp(
    payload: Mapping[str, Any], config: NormalizationConfig | None = None
) -> NormalizationResult:
    """Normalize one decoded OTLP trace-export payload without network I/O.

    Only explicit tool spans are selected. Every selected span must contain a valid OTLP trace ID,
    span ID, and positive start timestamp. Malformed security-mapping inputs fail closed.
    """

    effective_config = config or NormalizationConfig()
    resource_spans = _required_list(payload, "resourceSpans")
    observations: list[_ObservedSpan] = []
    observed_span_counts: dict[str, int] = defaultdict(int)

    for resource_index, resource_span_value in enumerate(resource_spans):
        resource_span = _required_mapping(resource_span_value, f"resourceSpans[{resource_index}]")
        resource = _optional_mapping(resource_span.get("resource"), "resource")
        resource_attributes = _decode_attributes(
            resource.get("attributes", []), "resource.attributes"
        )
        service_name = _string_or_default(resource_attributes.get("service.name"), "otel")
        server_id = _to_identifier(service_name, "service.name")
        service_version = _optional_string(
            resource_attributes.get("service.version"), "service.version"
        )
        if service_version is not None and len(service_version) > 64:
            raise OtlpFormatError("service.version must be at most 64 characters.")

        scope_spans = _required_list(resource_span, "scopeSpans")
        for scope_index, scope_span_value in enumerate(scope_spans):
            scope_span = _required_mapping(
                scope_span_value, f"resourceSpans[{resource_index}].scopeSpans[{scope_index}]"
            )
            scope = _optional_mapping(scope_span.get("scope"), "scope")
            scope_name = _string_or_default(scope.get("name"), "unknown")
            spans = _required_list(scope_span, "spans")
            for span_index, span_value in enumerate(spans):
                path = (
                    f"resourceSpans[{resource_index}].scopeSpans[{scope_index}].spans[{span_index}]"
                )
                span = _required_mapping(span_value, path)
                attributes = _decode_attributes(span.get("attributes", []), f"{path}.attributes")
                raw_tool_name = _first_string(attributes, effective_config.tool_name_keys)
                if raw_tool_name is None:
                    raw_trace_id = span.get("traceId")
                    if isinstance(raw_trace_id, str) and _TRACE_ID_RE.fullmatch(raw_trace_id):
                        observed_span_counts[raw_trace_id.lower()] += 1
                    continue

                trace_id = _validate_trace_id(span.get("traceId"), f"{path}.traceId")
                span_id = _validate_span_id(span.get("spanId"), f"{path}.spanId")
                parent_span_id = _optional_span_id(span.get("parentSpanId"), f"{path}.parentSpanId")
                timestamp_nanos, timestamp = _timestamp_from_nanos(
                    span.get("startTimeUnixNano"), f"{path}.startTimeUnixNano"
                )
                status_code = _status_code(span.get("status"), f"{path}.status")
                observed_span_counts[trace_id] += 1
                observations.append(
                    _ObservedSpan(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        timestamp=timestamp,
                        timestamp_nanos=timestamp_nanos,
                        tool_name=_to_identifier(raw_tool_name, "tool name"),
                        server_id=server_id,
                        tool_version=service_version,
                        actor=_actor_from_attributes(attributes, server_id, effective_config),
                        outcome=EventOutcome.FAILED if status_code == 2 else EventOutcome.SUCCEEDED,
                        side_effect=_side_effect_from_attributes(attributes, effective_config),
                        requested_capabilities=_capabilities_from_attributes(
                            attributes, effective_config
                        ),
                        input_labels=_labels_from_attributes(
                            attributes, effective_config.input_labels_key
                        ),
                        output_labels=_labels_from_attributes(
                            attributes, effective_config.output_labels_key
                        ),
                        service_name=service_name,
                        scope_name=scope_name,
                        status_code=status_code,
                    )
                )

    if not observations:
        diagnostic = NormalizationDiagnostic(
            code="no_eligible_tool_spans",
            message="The payload contains no spans with a configured tool-name attribute.",
        )
        return NormalizationResult(traces=(), diagnostics=(diagnostic,))

    grouped: dict[str, list[_ObservedSpan]] = defaultdict(list)
    for observation in observations:
        grouped[observation.trace_id].append(observation)

    traces = tuple(
        _build_trace(trace_id, grouped[trace_id], observed_span_counts[trace_id])
        for trace_id in sorted(grouped)
    )
    return NormalizationResult(traces=traces)


def normalize_json_lines(
    text: str, config: NormalizationConfig | None = None
) -> NormalizationResult:
    """Normalize one JSON document or a UTF-8 JSON Lines sequence of OTLP trace exports."""

    stripped = text.strip()
    if not stripped:
        raise OtlpFormatError(
            "Expected one OTLP JSON object or at least one non-empty JSON Lines record."
        )
    try:
        decoded_document = json.loads(stripped)
    except json.JSONDecodeError:
        payloads: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                decoded_line = json.loads(line)
            except json.JSONDecodeError as error:
                raise OtlpFormatError(
                    f"Line {line_number} is not valid JSON: {error.msg}"
                ) from error
            payloads.append(_required_mapping(decoded_line, f"line {line_number}"))
    else:
        payloads = [_required_mapping(decoded_document, "document")]
    if not payloads:
        raise OtlpFormatError(
            "Expected one OTLP JSON object or at least one non-empty JSON Lines record."
        )

    resource_spans: list[Any] = []
    for payload_index, payload in enumerate(payloads, start=1):
        try:
            resource_spans.extend(_required_list(payload, "resourceSpans"))
        except OtlpFormatError as error:
            raise OtlpFormatError(f"record {payload_index}: {error}") from error
    return normalize_otlp({"resourceSpans": resource_spans}, config=config)


def _build_trace(
    raw_trace_id: str, observations: Sequence[_ObservedSpan], observed_span_count: int
) -> WorkflowTrace:
    ordered = tuple(sorted(observations, key=lambda item: (item.timestamp_nanos, item.span_id)))
    event_ids = {item.span_id: f"otel.{item.span_id}" for item in ordered}
    if len(event_ids) != len(ordered):
        raise OtlpFormatError(f"Trace {raw_trace_id} contains duplicate selected spanId values.")

    events: list[ToolCall] = []
    for sequence, observation in enumerate(ordered, start=1):
        parent_event_id = None
        if observation.parent_span_id is not None:
            parent_index = next(
                (
                    index
                    for index, candidate in enumerate(ordered)
                    if candidate.span_id == observation.parent_span_id
                ),
                None,
            )
            if parent_index is not None and parent_index < sequence - 1:
                parent_event_id = event_ids[observation.parent_span_id]
        event_attributes: dict[str, Any] = {
            "otel": {
                "trace_id": raw_trace_id,
                "span_id": observation.span_id,
                "status_code": observation.status_code,
                "service_name": observation.service_name,
                "scope_name": observation.scope_name,
            }
        }
        if observation.parent_span_id is not None:
            event_attributes["otel"]["parent_span_id"] = observation.parent_span_id
        events.append(
            ToolCall(
                event_id=event_ids[observation.span_id],
                sequence=sequence,
                timestamp=observation.timestamp,
                actor=observation.actor,
                tool=ToolRef(
                    server_id=observation.server_id,
                    name=observation.tool_name,
                    version=observation.tool_version,
                ),
                outcome=observation.outcome,
                side_effect=observation.side_effect,
                parent_event_id=parent_event_id,
                requested_capabilities=observation.requested_capabilities,
                input_labels=observation.input_labels,
                output_labels=observation.output_labels,
                attributes=event_attributes,
            )
        )
    return WorkflowTrace(
        trace_id=f"otel.{raw_trace_id}",
        source="agentproof.otel",
        recorded_at=ordered[0].timestamp,
        events=tuple(events),
        metadata={
            "otel": {
                "adapter": "agentproof-otel",
                "adapter_version": "0.1",
                "trace_id": raw_trace_id,
                "observed_span_count": observed_span_count,
                "selected_span_count": len(ordered),
                "service_names": sorted({item.service_name for item in ordered}),
                "scope_names": sorted({item.scope_name for item in ordered}),
            }
        },
    )


def _required_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OtlpFormatError(f"{path} must be a JSON object.")
    return value


def _optional_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _required_mapping(value, path)


def _required_list(container: Mapping[str, Any], key: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise OtlpFormatError(f"{key} must be a JSON array.")
    return value


def _decode_attributes(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, list):
        raise OtlpFormatError(f"{path} must be a JSON array.")
    attributes: dict[str, Any] = {}
    for index, entry_value in enumerate(value):
        entry = _required_mapping(entry_value, f"{path}[{index}]")
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            raise OtlpFormatError(f"{path}[{index}].key must be a non-empty string.")
        if key in attributes:
            raise OtlpFormatError(f"{path} contains duplicate attribute key {key!r}.")
        attributes[key] = _decode_any_value(entry.get("value"), f"{path}[{index}].value")
    return attributes


def _decode_any_value(value: Any, path: str) -> Any:
    encoded = _required_mapping(value, path)
    supported_keys = (
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "arrayValue",
        "kvlistValue",
        "bytesValue",
    )
    present = [key for key in supported_keys if key in encoded]
    if len(present) != 1:
        raise OtlpFormatError(f"{path} must contain exactly one supported OTLP AnyValue variant.")
    key = present[0]
    raw_value = encoded[key]
    if key == "stringValue" or key == "bytesValue":
        if not isinstance(raw_value, str):
            raise OtlpFormatError(f"{path}.{key} must be a string.")
        return raw_value
    if key == "boolValue":
        if not isinstance(raw_value, bool):
            raise OtlpFormatError(f"{path}.boolValue must be a boolean.")
        return raw_value
    if key == "intValue":
        if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int)):
            raise OtlpFormatError(f"{path}.intValue must be an integer or decimal string.")
        try:
            return int(raw_value)
        except ValueError as error:
            raise OtlpFormatError(f"{path}.intValue is not a valid integer.") from error
    if key == "doubleValue":
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise OtlpFormatError(f"{path}.doubleValue must be a JSON number.")
        if not math.isfinite(float(raw_value)):
            raise OtlpFormatError(f"{path}.doubleValue must be finite.")
        return float(raw_value)
    if key == "arrayValue":
        array = _required_mapping(raw_value, f"{path}.arrayValue")
        values = _required_list(array, "values")
        return [
            _decode_any_value(item, f"{path}.arrayValue.values[{index}]")
            for index, item in enumerate(values)
        ]
    key_values = _required_mapping(raw_value, f"{path}.kvlistValue")
    return _decode_attributes(key_values.get("values", []), f"{path}.kvlistValue.values")


def _first_string(attributes: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise OtlpFormatError(f"{key} must be a non-empty string when present.")
        return value
    return None


def _string_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise OtlpFormatError("Expected a non-empty string OTLP attribute.")
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OtlpFormatError(f"{field_name} must be a non-empty string when present.")
    return value


def _to_identifier(value: str, field_name: str) -> str:
    normalized = _IDENTIFIER_CHARS_RE.sub("-", value.strip().lower())
    normalized = _IDENTIFIER_EDGE_RE.sub("", normalized)
    if not normalized:
        raise OtlpFormatError(f"{field_name} does not contain a usable identifier.")
    if len(normalized) > 128:
        raise OtlpFormatError(f"{field_name} exceeds the Core identifier limit of 128 characters.")
    return normalized


def _validate_trace_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _TRACE_ID_RE.fullmatch(value):
        raise OtlpFormatError(f"{path} must be a 32-character hexadecimal OTLP trace ID.")
    return value.lower()


def _validate_span_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _SPAN_ID_RE.fullmatch(value):
        raise OtlpFormatError(f"{path} must be a 16-character hexadecimal OTLP span ID.")
    return value.lower()


def _optional_span_id(value: Any, path: str) -> str | None:
    if value is None or value == "":
        return None
    return _validate_span_id(value, path)


def _timestamp_from_nanos(value: Any, path: str) -> tuple[int, datetime]:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OtlpFormatError(f"{path} must be a positive decimal nanosecond value.")
    try:
        nanoseconds = int(value)
    except ValueError as error:
        raise OtlpFormatError(f"{path} must be a valid integer.") from error
    if nanoseconds <= 0:
        raise OtlpFormatError(f"{path} must be positive.")
    seconds, remainder = divmod(nanoseconds, _NANOSECONDS_PER_SECOND)
    try:
        timestamp = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
            microsecond=remainder // 1_000
        )
    except (OverflowError, OSError, ValueError) as error:
        raise OtlpFormatError(f"{path} is outside the supported UTC datetime range.") from error
    return nanoseconds, timestamp


def _status_code(value: Any, path: str) -> int:
    if value is None:
        return 0
    status = _required_mapping(value, path)
    code = status.get("code", 0)
    if isinstance(code, bool) or not isinstance(code, int) or code not in {0, 1, 2}:
        raise OtlpFormatError(f"{path}.code must be an OTLP status integer in {{0, 1, 2}}.")
    return code


def _actor_from_attributes(
    attributes: Mapping[str, Any], server_id: str, config: NormalizationConfig
) -> ActorRef:
    raw_actor_id = attributes.get(config.actor_id_key, f"agent.{server_id}")
    if not isinstance(raw_actor_id, str) or not raw_actor_id.strip():
        raise OtlpFormatError(f"{config.actor_id_key} must be a non-empty string when present.")
    raw_kind = attributes.get(config.actor_kind_key, ActorKind.AGENT.value)
    if not isinstance(raw_kind, str):
        raise OtlpFormatError(f"{config.actor_kind_key} must be a string when present.")
    try:
        kind = ActorKind(raw_kind)
    except ValueError as error:
        raise OtlpFormatError(f"{config.actor_kind_key} is not a supported actor kind.") from error
    return ActorRef(actor_id=_to_identifier(raw_actor_id, config.actor_id_key), kind=kind)


def _side_effect_from_attributes(
    attributes: Mapping[str, Any], config: NormalizationConfig
) -> SideEffect:
    raw_value = attributes.get(config.side_effect_key, SideEffect.NONE.value)
    if not isinstance(raw_value, str):
        raise OtlpFormatError(f"{config.side_effect_key} must be a string when present.")
    try:
        return SideEffect(raw_value)
    except ValueError as error:
        raise OtlpFormatError(
            f"{config.side_effect_key} is not a supported side effect."
        ) from error


def _capabilities_from_attributes(
    attributes: Mapping[str, Any], config: NormalizationConfig
) -> tuple[str, ...]:
    raw_values = attributes.get(config.requested_capabilities_key, [])
    if not isinstance(raw_values, list) or not all(isinstance(value, str) for value in raw_values):
        raise OtlpFormatError(f"{config.requested_capabilities_key} must be an array of strings.")
    values = tuple(_to_identifier(value, config.requested_capabilities_key) for value in raw_values)
    if len(values) != len(set(values)):
        raise OtlpFormatError(f"{config.requested_capabilities_key} may not contain duplicates.")
    return values


def _labels_from_attributes(
    attributes: Mapping[str, Any], attribute_key: str
) -> tuple[DataClassification, ...]:
    raw_values = attributes.get(attribute_key, [])
    if not isinstance(raw_values, list) or not all(isinstance(value, str) for value in raw_values):
        raise OtlpFormatError(f"{attribute_key} must be an array of strings.")
    try:
        return tuple(DataClassification(value) for value in raw_values)
    except ValueError as error:
        raise OtlpFormatError(
            f"{attribute_key} contains an unsupported data classification."
        ) from error
