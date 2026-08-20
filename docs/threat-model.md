# Threat Model

## Assets

The asset is a reproducible, privacy-minimized workflow-evidence trace. The adapter must not turn
untrusted telemetry into invented authorization facts or silently hide malformed mapping inputs.

## Trust boundaries

OTLP JSON is untrusted input. The adapter validates shape, identifiers, timestamps, `AnyValue`
encoding, mapped security values, and duplicate span identity before constructing Core models. Core
then validates ordered event and parent invariants.

| Threat | Control |
|---|---|
| Prompt, secret, or personal-data leakage through attributes | Allowlist mapping; exclusion of arbitrary attributes, events, and GenAI content fields. |
| A generic span becoming a security-relevant tool action | Explicit tool-name eligibility only. |
| Telemetry order changing a security conclusion | Sort by start timestamp and span ID; preserve source IDs. |
| Invalid labels or side effects lowering severity by accident | Explicit enum parsing; malformed mapped values fail closed. |
| Parent relationship ambiguity | Retain a parent only when it is an earlier selected span; otherwise preserve provenance without asserting workflow parentage. |
| Resource exhaustion through remote ingest | No network listener or collector exists in this package; callers control local file input. |

## Non-goals

This package does not authenticate exporters, operate a telemetry collector, redact raw source files,
evaluate security policy, replay actions, attest a trace’s truthfulness, or execute a live tool.
