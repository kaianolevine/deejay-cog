"""
Application entrypoint for deejay-cog.

Registers a single router-style Prefect deployment (`deejay-cog/deejay-cog`)
and starts a runner loop that polls for scheduled or manually triggered runs.

The router flow dispatches to one of the underlying production flows based
on the `mode` parameter. Callers (watcher-cog, Prefect UI, REST API, CLI)
must pass `mode` explicitly; an unknown or missing mode raises ValueError.

Supported modes:
    - "process-new-files"   → process_new_csv_files_flow
    - "ingest-live-history" → ingest_live_history

Railway start command: python -m deejay_cog.main

All flows run in-process on Railway with full access to environment
variables. No work pool required.

On Railway restart, any in-flight runs are interrupted and Prefect Cloud
marks them as crashed. The on_crashed hooks in each underlying flow handle
crash reporting to evaluator-cog automatically.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import sentry_sdk
from dotenv import load_dotenv
from prefect import flow, serve
from prefect.flows import flow as prefect_flow

from deejay_cog.ingest_live_history import ingest_live_history
from deejay_cog.process_new_files import process_new_csv_files_flow

# Map of supported router modes to the underlying flow functions.
# Adding a new mode? Register it here and document it in the module docstring.
_MODE_DISPATCH = {
    "process-new-files": process_new_csv_files_flow,
    "ingest-live-history": ingest_live_history,
}


@flow(name="deejay-cog")
def deejay_router(mode: str | None = None) -> Any:
    """Single entrypoint flow that dispatches to a sub-flow by `mode`.

    Parameters
    ----------
    mode:
        Which underlying flow to run. Required. One of:
        "process-new-files", "ingest-live-history".

    Raises
    ------
    ValueError
        If `mode` is missing or not a recognized dispatch key. We never
        silently default — every caller must opt in to a behavior.
    """
    if not mode:
        raise ValueError(
            "deejay router requires a `mode` parameter. "
            f"Supported modes: {sorted(_MODE_DISPATCH)}"
        )

    target = _MODE_DISPATCH.get(mode)
    if target is None:
        raise ValueError(
            f"Unknown deejay router mode: {mode!r}. "
            f"Supported modes: {sorted(_MODE_DISPATCH)}"
        )

    return target()


def main() -> None:
    """Register the deejay router flow and start the Prefect runner loop."""
    load_dotenv()
    sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"), environment="production")

    src_path = os.environ.get(
        "APP_SOURCE_PATH", str(Path(__file__).parent.parent.parent)
    )

    router = prefect_flow.from_source(
        source=src_path,
        entrypoint="src/deejay_cog/main.py:deejay_router",
    )

    serve(router.to_deployment(name="deejay-cog"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
