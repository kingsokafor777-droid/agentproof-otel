from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from agentproof_core import DataClassification, EventOutcome, SideEffect

from agentproof_otel import (
    NormalizationConfig,
    OtlpFormatError,
    normalize_json_lines,
    normalize_otlp,
)


def _spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _attributes(span: dict[str, Any]) -> list[dict[str, Any]]:
    return span["attributes"]


def test_normalizes_selected_tool_spans_with_compact_provenance(
    safe_payload: dict[str, Any],
) -> None:
    result = normalize_otlp(safe_payload)

    assert result.diagnostics == ()
    assert len(result.traces) == 1
    trace = result.traces[0]
    first, second = trace.events
    assert trace.trace_id == "otel.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert trace.source == "agentproof.otel"
    assert trace.metadata["otel"]["observed_span_count"] == 3
    assert trace.metadata["otel"]["selected_span_count"] == 2
    assert first.event_id == "otel.0000000000000002"
    assert first.tool.server_id == "support-agent"
    assert first.tool.name == "get-ticket"
    assert first.tool.version == "1.4.0"
    assert first.actor.actor_id == "agent.support"
    assert first.side_effect == SideEffect.READ
    assert first.requested_capabilities == ("ticket.read",)
    assert first.input_labels == (DataClassification.INTERNAL,)
    assert second.event_id == "otel.0000000000000003"
    assert second.parent_event_id == first.event_id
    assert second.outcome == EventOutcome.FAILED
    assert second.side_effect == SideEffect.WRITE
    assert second.output_labels == (DataClassification.CONFIDENTIAL,)
    assert "gen_ai.tool.call.arguments" not in first.attributes
    assert "gen_ai.tool.call.result" not in second.attributes
    assert trace.fingerprint() == normalize_otlp(safe_payload).traces[0].fingerprint()


def test_deterministically_orders_equal_timestamp_by_span_id(safe_payload: dict[str, Any]) -> None:
    payload = copy.deepcopy(safe_payload)
    spans = _spans(payload)
    spans[1]["startTimeUnixNano"] = "1710000000000002000"
    result = normalize_otlp(payload)

    assert [event.event_id for event in result.traces[0].events] == [
        "otel.0000000000000002",
        "otel.0000000000000003",
    ]


def test_parent_of_unselected_span_is_not_asserted_as_workflow_parent(
    safe_payload: dict[str, Any],
) -> None:
    payload = copy.deepcopy(safe_payload)
    _spans(payload)[1]["parentSpanId"] = "0000000000000001"

    first = normalize_otlp(payload).traces[0].events[0]
    assert first.parent_event_id is None
    assert first.attributes["otel"]["parent_span_id"] == "0000000000000001"


def test_empty_selection_returns_diagnostic(safe_payload: dict[str, Any]) -> None:
    payload = copy.deepcopy(safe_payload)
    for span in _spans(payload):
        span["attributes"] = []

    result = normalize_otlp(payload)
    assert result.traces == ()
    assert result.diagnostics[0].code == "no_eligible_tool_spans"


def test_accepts_pretty_json_document_and_json_lines(safe_payload: dict[str, Any]) -> None:
    document_result = normalize_json_lines(json.dumps(safe_payload, indent=2))
    second = copy.deepcopy(safe_payload)
    for span in _spans(second):
        span["traceId"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    lines_result = normalize_json_lines("\n".join((json.dumps(safe_payload), json.dumps(second))))

    assert len(document_result.traces) == 1
    assert [trace.trace_id for trace in lines_result.traces] == [
        "otel.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "otel.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]


def test_accepts_configured_tool_name_alias(safe_payload: dict[str, Any]) -> None:
    payload = copy.deepcopy(safe_payload)
    span = _spans(payload)[1]
    span["attributes"] = [{"key": "custom.tool", "value": {"stringValue": "Fetch Case"}}]
    _spans(payload)[2]["attributes"] = []

    result = normalize_otlp(payload, config=NormalizationConfig(tool_name_keys=("custom.tool",)))
    assert result.traces[0].events[0].tool.name == "fetch-case"


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ("bad_trace", "trace ID"),
        ("zero_time", "must be positive"),
        ("bad_effect", "side effect"),
        ("duplicate_span", "duplicate selected spanId"),
        ("bad_any_value", "exactly one supported"),
    ],
)
def test_rejects_malformed_or_unsafe_mappings(
    safe_payload: dict[str, Any], change: str, match: str
) -> None:
    payload = copy.deepcopy(safe_payload)
    spans = _spans(payload)
    if change == "bad_trace":
        spans[1]["traceId"] = "not-a-trace-id"
    elif change == "zero_time":
        spans[1]["startTimeUnixNano"] = "0"
    elif change == "bad_effect":
        _attributes(spans[1])[3]["value"] = {"stringValue": "unsafe"}
    elif change == "duplicate_span":
        spans[2]["spanId"] = spans[1]["spanId"]
    else:
        _attributes(spans[1])[0]["value"] = {"stringValue": "tool", "intValue": "1"}

    with pytest.raises(OtlpFormatError, match=match):
        normalize_otlp(payload)


def test_rejects_empty_or_invalid_json_lines() -> None:
    with pytest.raises(OtlpFormatError, match="Expected one"):
        normalize_json_lines("\n \n")
    with pytest.raises(OtlpFormatError, match="not valid JSON"):
        normalize_json_lines("not-json")
