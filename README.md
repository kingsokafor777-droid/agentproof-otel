# AgentProof OTel

> **Offline OpenTelemetry ingestion and deterministic agent-span normalization for AgentProof.**

AgentProof OTel translates captured OTLP JSON traces into the strict, versioned
`agentproof-core` `WorkflowTrace` model. It selects explicit agent-tool spans, preserves compact
provenance, and rejects malformed security mappings. It does **not** run a collector, open a network
connection, execute tools, infer authorization, or copy prompts, tool arguments, tool results, or
arbitrary span attributes into AgentProof evidence.

## Supported input and output

The importer accepts decoded OTLP JSON trace-export payloads with the standard
`resourceSpans → scopeSpans → spans` envelope. A file may contain one payload or UTF-8 JSON Lines
containing many payloads. It selects a span only when `gen_ai.tool.name` or the explicit override
`agentproof.tool.name` is present.

| OTLP signal | AgentProof output | Default behavior |
|---|---|---|
| `traceId` / `spanId` | Stable Core trace/event identifiers | Normalized as `otel.<lowercase-id>`. |
| `startTimeUnixNano` | Ordered event timestamp | Events sort by start time, then span ID. |
| `parentSpanId` | Parent event relationship | Retained only where it points to an earlier selected tool span. |
| `service.name` | Tool server ID | Falls back to `otel` when absent. |
| `gen_ai.tool.name` | Tool name | Generic, model, retrieval, and prompt spans are ignored. |
| status code `2` | Failed outcome | Other OTLP status values map to succeeded. |
| `agentproof.*` attributes | Explicit security mapping | Optional side effect, labels, capabilities, and actor fields only. |

Read the [architecture](docs/architecture.md), [mapping and privacy policy](docs/mapping.md),
[compatibility policy](docs/compatibility.md), and [threat model](docs/threat-model.md) before using
the package in a security workflow.

## Install

The package depends on the released Core v0.1 line. For local development:

```bash
python -m pip install -e "../agentproof-core"
python -m pip install -e ".[dev]"
```

## Quickstart

```bash
agentproof-otel normalize fixtures/safe-tool-trace.otlp.json > normalized.json
```

The command writes an envelope containing `traces` and non-fatal `diagnostics`. Every trace in the
envelope is valid `agentproof-core` JSON and can be passed to the Core CLI or later policy/replay
tools.

## Security posture

> OpenTelemetry attributes can contain secrets and personal data. AgentProof OTel is deliberately
> allowlist-based: it copies only documented, compact mapping and provenance fields.

Security meaning is never guessed. `approval`, data labels, requested capabilities, and side effects
remain Core concepts and are populated only by explicit `agentproof.*` attributes. An invalid mapped
value fails closed. Consult [SECURITY.md](SECURITY.md) before sharing an issue or fixture.

## Repository boundaries

| This repository owns | This repository does not own |
|---|---|
| Offline OTLP JSON and JSON Lines decoding | OTLP transport, collector deployment, or SDK export |
| Tool-span eligibility and deterministic Core normalization | Policy parsing/evaluation or workflow replay |
| Provenance-preserving, privacy-minimizing mapping | MCP transcript ingestion or tool-definition provenance |
| A small local/CI CLI | Hosted storage, dashboards, credentials, or live tool execution |

## Development

```bash
ruff check .
ruff format --check .
mypy
pytest
python -m build
twine check dist/*
```

The suite uses only sealed fixtures and local parsing; it requires no credentials, OTLP collector,
network service, or live agent/tool call.

## License

Licensed under the [Apache License 2.0](LICENSE).
