# Contributing to AgentProof OTel

Contributions must preserve the adapter’s narrow boundary: no network client, collector lifecycle,
live agent execution, credential fixture, prompt payload capture, or undocumented policy semantics.

Before proposing a mapping change, add a sealed OTLP fixture and a deterministic test demonstrating
its Core output. Changes to default identifiers, ordering, provenance fields, or existing mappings
require a documented compatibility review. Run the complete local validation command set from the
README before opening a pull request.
