# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for this
repository. ADRs document significant architectural decisions, the
context around them, and their consequences.

## Format

Each ADR is a markdown file named `ADR-NNN-short-slug.md` where `NNN`
is a zero-padded three-digit sequence number starting at `001`. This
matches the ecosystem-standards DOC-005 specification.

Each ADR uses three sections: **Context** (what forces motivated the
decision), **Decision** (what change is being made), and
**Consequences** (what becomes easier or harder).

## Index

- [ADR-001: Run Prefect flows in-process via prefect.serve()](./ADR-001-prefect-in-process-serve.md)
- [ADR-002: Production-vs-local flow tiers](./ADR-002-production-vs-local-flow-tiers.md)
- [ADR-003: On-demand cog with no expected cadence](./ADR-003-on-demand-no-cadence.md)
- [ADR-004: Best-effort pipeline evaluation posting](./ADR-004-best-effort-pipeline-eval.md)
