from __future__ import annotations

import copy
from typing import Any

import pytest
from agentproof_core import ActorKind, DataClassification

import agentproof_otel.normalize as normalizer
from agentproof_otel import (
    NormalizationDiagnostic,
    NormalizationResult,
    OtlpFormatError,
    normalize_json_lines,
)


def _first_tool_attributes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["attributes"]


def test_result_and_diagnostic_serialization() -> None:
    diagnostic = NormalizationDiagnostic(code="example", message="message")
    result = NormalizationResult(traces=(), diagnostics=(diagnostic,))

    assert diagnostic.to_dict() == {"code": "example", "message": "message"}
    assert result.to_dict() == {"traces": [], "diagnostics": [diagnostic.to_dict()]}


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ({"stringValue": "value"}, "value"),
        ({"bytesValue": "YWJj"}, "YWJj"),
        ({"boolValue": True}, True),
        ({"intValue": "7"}, 7),
        ({"intValue": 8}, 8),
        ({"doubleValue": 1.5}, 1.5),
        ({"arrayValue": {"values": [{"stringValue": "a"}]}}, ["a"]),
        (
            {"kvlistValue": {"values": [{"key": "nested", "value": {"boolValue": True}}]}},
            {"nested": True},
        ),
    ],
)
def test_decodes_supported_otlp_any_value_variants(encoded: dict[str, Any], expected: Any) -> None:
    assert normalizer._decode_any_value(encoded, "value") == expected


@pytest.mark.parametrize(
    ("encoded", "match"),
    [
        (None, "JSON object"),
        ({}, "exactly one"),
        ({"stringValue": "a", "intValue": "1"}, "exactly one"),
        ({"stringValue": 1}, "must be a string"),
        ({"boolValue": "true"}, "must be a boolean"),
        ({"intValue": True}, "integer or decimal"),
        ({"intValue": "not-int"}, "not a valid integer"),
        ({"doubleValue": True}, "JSON number"),
        ({"doubleValue": float("inf")}, "finite"),
        ({"arrayValue": {}}, "values must be a JSON array"),
        ({"kvlistValue": "not-object"}, "JSON object"),
    ],
)
def test_rejects_invalid_otlp_any_value_variants(encoded: Any, match: str) -> None:
    with pytest.raises(OtlpFormatError, match=match):
        normalizer._decode_any_value(encoded, "value")


@pytest.mark.parametrize(
    ("value", "path", "match"),
    [
        ("not-a-list", "attributes", "JSON array"),
        (["not-an-object"], "attributes", "JSON object"),
        ([{"key": "", "value": {"stringValue": "x"}}], "attributes", "non-empty string"),
        (
            [
                {"key": "same", "value": {"stringValue": "x"}},
                {"key": "same", "value": {"stringValue": "y"}},
            ],
            "attributes",
            "duplicate attribute",
        ),
    ],
)
def test_rejects_invalid_attribute_lists(value: Any, path: str, match: str) -> None:
    with pytest.raises(OtlpFormatError, match=match):
        normalizer._decode_attributes(value, path)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "fallback"), ("service", "service")],
)
def test_string_defaults(value: Any, expected: str) -> None:
    assert normalizer._string_or_default(value, "fallback") == expected


@pytest.mark.parametrize(
    ("function", "value", "match"),
    [
        (normalizer._string_or_default, "", "non-empty string"),
        (normalizer._optional_string, "", "non-empty string"),
        (normalizer._to_identifier, "!!!", "usable identifier"),
        (normalizer._to_identifier, "x" * 129, "128 characters"),
        (normalizer._validate_trace_id, "not-a-trace", "trace ID"),
        (normalizer._validate_span_id, "not-a-span", "span ID"),
        (normalizer._timestamp_from_nanos, True, "decimal nanosecond"),
        (normalizer._timestamp_from_nanos, "not-a-number", "valid integer"),
        (normalizer._status_code, {"code": 3}, "status integer"),
    ],
)
def test_rejects_scalar_boundary_violations(function: Any, value: Any, match: str) -> None:
    with pytest.raises(OtlpFormatError, match=match):
        if function is normalizer._string_or_default:
            function(value, "fallback")
        else:
            function(value, "field")


def test_optional_mapping_and_identifier_helpers() -> None:
    assert normalizer._optional_mapping(None, "optional") == {}
    assert normalizer._optional_string(None, "optional") is None
    assert normalizer._to_identifier(" Support Agent / Read ", "tool") == "support-agent-read"
    assert normalizer._optional_span_id(None, "parent") is None
    assert normalizer._optional_span_id("", "parent") is None
    assert normalizer._status_code(None, "status") == 0
    assert normalizer._status_code({"code": 1}, "status") == 1


def test_rejects_invalid_actor_security_and_label_mappings() -> None:
    config = normalizer.NormalizationConfig()
    with pytest.raises(OtlpFormatError, match=r"actor.id"):
        normalizer._actor_from_attributes({config.actor_id_key: 1}, "server", config)
    with pytest.raises(OtlpFormatError, match=r"actor.kind"):
        normalizer._actor_from_attributes({config.actor_kind_key: "unknown"}, "server", config)
    with pytest.raises(OtlpFormatError, match="side_effect"):
        normalizer._side_effect_from_attributes({config.side_effect_key: 1}, config)
    with pytest.raises(OtlpFormatError, match="requested_capabilities"):
        normalizer._capabilities_from_attributes(
            {config.requested_capabilities_key: "read"}, config
        )
    with pytest.raises(OtlpFormatError, match="duplicates"):
        normalizer._capabilities_from_attributes(
            {config.requested_capabilities_key: ["data.read", "data.read"]}, config
        )
    with pytest.raises(OtlpFormatError, match="output_labels"):
        normalizer._labels_from_attributes({"output_labels": ["secret"]}, "output_labels")


def test_maps_default_actor_and_empty_security_collections() -> None:
    config = normalizer.NormalizationConfig()
    actor = normalizer._actor_from_attributes({}, "support", config)

    assert actor.kind == ActorKind.AGENT
    assert actor.actor_id == "agent.support"
    assert normalizer._capabilities_from_attributes({}, config) == ()
    assert normalizer._labels_from_attributes({}, "labels") == ()
    assert normalizer._labels_from_attributes({"labels": ["public"]}, "labels") == (
        DataClassification.PUBLIC,
    )


def test_rejects_malformed_payload_containers_and_optional_objects(
    safe_payload: dict[str, Any],
) -> None:
    payload = copy.deepcopy(safe_payload)
    with pytest.raises(OtlpFormatError, match="resourceSpans must be a JSON array"):
        normalizer.normalize_otlp({"resourceSpans": {}})
    payload["resourceSpans"][0]["resource"] = "not-object"
    with pytest.raises(OtlpFormatError, match="resource must be a JSON object"):
        normalizer.normalize_otlp(payload)
    payload = copy.deepcopy(safe_payload)
    payload["resourceSpans"][0]["scopeSpans"] = {}
    with pytest.raises(OtlpFormatError, match="scopeSpans must be a JSON array"):
        normalizer.normalize_otlp(payload)


def test_rejects_invalid_service_version_and_empty_tool_name(safe_payload: dict[str, Any]) -> None:
    payload = copy.deepcopy(safe_payload)
    payload["resourceSpans"][0]["resource"]["attributes"][1]["value"] = {"stringValue": "x" * 65}
    with pytest.raises(OtlpFormatError, match=r"service.version"):
        normalizer.normalize_otlp(payload)

    payload = copy.deepcopy(safe_payload)
    _first_tool_attributes(payload)[0]["value"] = {"stringValue": " "}
    with pytest.raises(OtlpFormatError, match=r"tool.name"):
        normalizer.normalize_otlp(payload)


def test_json_line_record_context_for_missing_envelope() -> None:
    with pytest.raises(OtlpFormatError, match="record 1"):
        normalize_json_lines("{}")
