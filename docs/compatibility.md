# Compatibility Policy

AgentProof OTel follows semantic versioning and supports `agentproof-core >=0.1.0,<0.2.0` in its
first minor line. The Core project governs the `WorkflowTrace` schema. This project governs OTLP
input support, adapter configuration, mapping behavior, and provenance layout.

| Change | Versioning treatment |
|---|---|
| New opt-in configuration or additive accepted alias | Minor release |
| New excluded attribute or diagnostic | Minor release when it does not change existing output |
| Changed default ID, event order, parent relationship, mapping, or provenance field | Major release |
| Security fix that preserves normalized output where possible | Patch release |

OpenTelemetry GenAI conventions are evolving separately. A semantic-convention change is supported
only after it has a fixture, deterministic mapping test, documentation update, and compatibility
classification in this repository.
