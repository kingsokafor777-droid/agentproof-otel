# AgentProof OTel Architecture

## Purpose

`agentproof-otel` is the offline translation boundary between OpenTelemetry trace exports and the
strict `agentproof-core` `WorkflowTrace` contract. It converts observed **tool-invocation spans**
into ordered AgentProof events; it does not decide whether a tool action was authorized or safe.

## Supported input

Version `0.1` accepts decoded **OTLP JSON** `ExportTraceServiceRequest` objects using the
`resourceSpans → scopeSpans → spans` envelope. The command-line interface accepts either one JSON
object or a UTF-8 JSON Lines file containing one trace-export object per line. OTLP JSON uses
lower-camel-case field names and hexadecimal `traceId` / `spanId` values.[1]

The importer deliberately does not open a socket, operate an OTLP collector, decode binary
Protobuf, call an OpenTelemetry SDK, or perform live tool execution. A caller supplies an already
captured payload or file.

## Eligibility and source mapping

A span is eligible only when it contains a configured tool-name attribute. The default ordered
lookup is:

| Rank | Attribute | Purpose |
|---:|---|---|
| 1 | `gen_ai.tool.name` | Current documented GenAI tool name. |
| 2 | `agentproof.tool.name` | Explicit application-side adapter override. |

The mapping intentionally does **not** turn model, chat, retrieval, or generic server spans into
tool calls. OpenTelemetry GenAI semantic conventions remain separately versioned and evolving, so
the importer keeps semantic-key selection in `NormalizationConfig` rather than copying those
conventions into AgentProof Core.[2]

| OTLP source | AgentProof Core target | Rule |
|---|---|---|
| `traceId` | `WorkflowTrace.trace_id` | `otel.<lowercase trace ID>`; one normalized trace per OTLP trace ID. |
| `spanId` | `ToolCall.event_id` | `otel.<lowercase span ID>`; duplicates in one trace are rejected. |
| `parentSpanId` | `ToolCall.parent_event_id` | Retained only when the parent is another eligible, earlier event; otherwise preserved as provenance only. |
| `startTimeUnixNano` | `ToolCall.timestamp` | Parsed exactly as a UTC instant; invalid or non-positive values are rejected. |
| span status | `ToolCall.outcome` | OTLP `ERROR` (`2`) maps to `failed`; unset / `OK` map to `succeeded`. |
| configured tool-name attribute | `ToolRef.name` | Canonicalized to the Core identifier grammar. |
| `service.name` resource attribute | `ToolRef.server_id` | Canonicalized service identifier, with an `otel` fallback. |

Events sort by `(startTimeUnixNano, spanId)` so normalization is repeatable even when JSON Lines
or exporters do not preserve input order. Core’s contiguous sequencing and parent-before-child
invariants remain the final validation boundary.

## Security and provenance

> Telemetry is evidence, not authorization. The adapter never infers approval, requested
> capability, data classification, or side-effect severity from an OpenTelemetry span by default.

Those security meanings can enter the normalized event only through explicit namespaced
`agentproof.*` attributes. The default mappings are `agentproof.side_effect`,
`agentproof.requested_capabilities`, `agentproof.input_labels`, and `agentproof.output_labels`.
Unknown attributes are excluded. Prompt content, outputs, tool arguments, tool results, headers,
and arbitrary attributes are not copied into a Core trace.

Each trace records compact, deterministic provenance in `WorkflowTrace.metadata`: adapter name and
version, OTLP trace ID, selected-event count, observed-span count, resource service names, and
instrumentation-scope names. Each event records its OTel trace/span identifiers, status code,
scope, and service mapping in `ToolCall.attributes`. This preserves audit linkage while avoiding
automatic capture of high-risk payload content.

## Public API

| Symbol | Role |
|---|---|
| `NormalizationConfig` | Frozen configuration for eligible tool-name keys and namespaced security-attribute mappings. |
| `normalize_otlp` | Converts one decoded OTLP JSON object to a deterministic `NormalizationResult`. |
| `normalize_json_lines` | Converts a UTF-8 OTLP JSON Lines payload without relying on file ordering. |
| `NormalizationResult` | Contains normalized Core traces and structured non-fatal diagnostics. |
| `OtlpFormatError` | Raised for malformed OTLP shape, IDs, timestamps, attributes, or duplicate span identity. |

The command-line interface provides `agentproof-otel normalize <path>` and emits canonical
AgentProof trace JSON to standard output. It is intended for local inspection and CI fixtures, not
collector deployment.

## Compatibility policy

`agentproof-otel` version `0.1.x` supports `agentproof-core >=0.1.0,<0.2.0`. The Core package owns
the normalized model and schema compatibility; this package owns OTLP decoding and mapping policy.
Additive tool-name aliases and opt-in attribute mappings are minor-version changes. Changes that
alter an existing default mapping, normalized ID, ordering, or provenance field require a major
version. Unsupported or evolving GenAI attributes stay outside the default mapping until covered
by deterministic fixtures and a documented compatibility update.

## Release acceptance criteria

The first public release must demonstrate all of the following offline and deterministically:

1. A sealed OTLP JSON fixture yields a Core-valid trace with deterministic ordering, IDs, parent
   relationships, fingerprints, and provenance.
2. Tool-span filtering excludes model/prompt/result content, generic spans, and unknown attributes.
3. Explicit namespaced side-effect, capability, and label attributes map only to valid Core values.
4. Malformed IDs, invalid timestamps, duplicate span IDs, malformed `AnyValue` payloads, and invalid
   mapped security values fail closed with actionable errors.
5. JSON and JSON Lines ingestion, CLI output, strict linting, formatting, static typing, at least
   95% branch coverage, generated artifacts, source/wheel construction, and metadata validation
   pass without credentials, network I/O, a collector, or live tool execution.

## References

[1]: https://opentelemetry.io/docs/specs/otlp/ "OpenTelemetry Protocol specification"
[2]: https://github.com/open-telemetry/semantic-conventions-genai "OpenTelemetry GenAI Semantic Conventions"
