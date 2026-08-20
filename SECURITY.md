# Security Policy

## Reporting a vulnerability

Please report potential vulnerabilities privately through the repository’s security-advisory channel.
Do not include credentials, production OTLP exports, prompts, tool arguments, tool results, personal
data, access tokens, or live destructive-action details in a public issue.

## Fixture policy

Fixtures must be sealed, synthetic, reviewable, and deterministic. Unknown OpenTelemetry attributes
are intentionally excluded from normalized AgentProof evidence; do not rely on that behavior as a
substitute for upstream telemetry data classification and redaction.
