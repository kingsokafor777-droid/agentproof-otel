# Mapping and Privacy Policy

## Default allowlist

AgentProof OTel decodes OTLP attributes only to make a narrow selection and mapping decision. It
does not copy an OTLP attribute unless it appears in the following table.

| Attribute | Mapping | Validation |
|---|---|---|
| `gen_ai.tool.name`, `agentproof.tool.name` | `ToolRef.name` | First present non-empty string wins; name is canonicalized to Core’s identifier grammar. |
| `service.name` | `ToolRef.server_id` and provenance | Non-empty string; falls back to `otel`. |
| `service.version` | `ToolRef.version` | Optional string, maximum 64 characters. |
| `agentproof.actor.id`, `agentproof.actor.kind` | `ActorRef` | Explicit identifier and one of Core’s actor kinds. |
| `agentproof.side_effect` | `ToolCall.side_effect` | One of `none`, `read`, `write`, or `irreversible`. |
| `agentproof.requested_capabilities` | `ToolCall.requested_capabilities` | Array of unique Core identifiers. |
| `agentproof.input_labels`, `agentproof.output_labels` | Data classification labels | Arrays of `public`, `internal`, `confidential`, or `restricted`. |

The event provenance object holds the OTel trace/span/parent identifiers, status code, scope name,
and service name. The trace provenance object holds only the OTel trace ID, adapter version,
event/span counts, service names, and scope names.

## Explicit exclusions

The following remain excluded even if an exporter emits them: `gen_ai.input.messages`,
`gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.call.arguments`,
`gen_ai.tool.call.result`, `http.*`, `url.*`, headers, resource attributes other than service name
and version, unknown `agentproof.*` attributes, span events, and links. Applications needing richer
evidence should add a reviewed, versioned mapping rather than treating raw telemetry as policy data.
