# ADR-001: Run Prefect flows in-process via prefect.serve()

Date: 2026-04-20

## Status

Accepted

## Context

Prefect Cloud supports two primary deployment patterns:

1. **Work pools** - flows are registered with a work pool; separate
   worker processes pull work from the pool and execute flows.
2. **Serve** - a single process registers flows and stays alive,
   handling scheduled and manually triggered runs itself.

Work pools are more flexible for heterogeneous fleets (multi-cloud,
multi-runtime, dynamic scaling). Serve is simpler for single-service
deployments where one container runs one cog's flows.

Prefect Cloud's hobby tier caps deployments at 5 across the account.
That made multi-deployment work-pool fleets expensive per-slot, and
made serve the better match for this ecosystem's one-cog-per-service
architecture.

## Decision

`deejay-cog` registers its flows via `prefect.serve()` in `main.py` as
the Railway service's `python -m deejay_cog.main` entry point. Flows
run in-process with full access to environment variables and Doppler
secrets. No work pool is configured.

`main.py` serves two deployments:

- `process-new-files` - `process_new_csv_files_flow`
- `ingest-live-history` - `ingest_live_history`

Other flows (`generate_summaries`, `update_deejay_set_collection`,
`retag_music`) are deferred - not served on Railway because they are
run manually or on a different cadence.

## Consequences

**Easier:**

- Single process model matches Railway's service-container pattern.
- No work pool infrastructure to maintain.
- Environment variables and Doppler-injected secrets available
  directly to flow code without a separate worker bootstrap.
- On Railway restart, in-flight runs are interrupted and Prefect
  marks them as crashed. The `on_crashed` hooks in each flow handle
  crash reporting to evaluator-cog.

**Harder:**

- Horizontal scaling requires running multiple service instances,
  each serving the same flows. Prefect handles concurrency correctly
  via run leases, but resource planning is per-container.
- Adding a new served flow means editing `main.py` and counts
  against the 5-deployment hobby-tier cap.

## References

- ecosystem-standards CD-015 (prefect.serve is the canonical pattern)
- ecosystem-standards PIPE-008 (repository_dispatch retired in favor
  of prefect.serve)
