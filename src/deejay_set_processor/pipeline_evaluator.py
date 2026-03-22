"""Best-effort Claude evaluation of pipeline runs; posts findings to deejay-marvel-api."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any

from kaiano import logger as logger_mod

log = logger_mod.get_logger()

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _anthropic_messages_create(
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    user_prompt: str,
) -> str:
    import httpx

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    with httpx.Client(timeout=120.0) as client:
        r = client.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    blocks = data.get("content") or []
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(str(b.get("text", "")))
    return "".join(parts).strip()


def _parse_findings_json(text: str) -> list[dict[str, Any]]:
    raw = text.strip()
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict) and "findings" in parsed:
        inner = parsed["findings"]
        return inner if isinstance(inner, list) else []
    if isinstance(parsed, list):
        return parsed
    return []


def _build_prompt_csv(
    *,
    run_id: str,
    standards_version: str,
    sets_imported: int,
    sets_failed: int,
    sets_skipped: int,
    total_tracks: int,
    failed_set_labels: list[str],
    api_ingest_success: bool,
    sets_attempted: int,
    unrecognized_filename_skips: int,
    duplicate_csv_count: int,
) -> str:
    failed_labels = ", ".join(failed_set_labels) if failed_set_labels else "(none)"
    return f"""You are evaluating a DJ set CSV processing pipeline run against engineering standards v{standards_version}.

CSV PROCESSING evaluation context:
- GitHub Actions run_id: {run_id}
- sets_attempted: CSV files encountered for processing ({sets_attempted})
- sets_imported: successfully processed CSVs (uploaded as Google Sheet, moved to archive) ({sets_imported})
- sets_failed: CSVs renamed with FAILED_ prefix ({sets_failed})
- sets_skipped: non-CSV files moved out of the source folder ({sets_skipped})
- unrecognized_filename_skips: files skipped due to filename format ({unrecognized_filename_skips})
- possible_duplicate_csv: CSVs renamed as possible_duplicate_ and not uploaded ({duplicate_csv_count})
- total_tracks: total track rows across successfully processed sets ({total_tracks})
- failed_set_labels: {failed_labels}
- api_ingest_success: all API ingest attempts succeeded, or none were required ({api_ingest_success})

Respond with ONLY valid JSON (no markdown) in this exact shape:
{{"findings":[{{"dimension":"pipeline_consistency","severity":"INFO|WARN|ERROR","finding":"...","suggestion":""}}]}}

Rules:
- severity must be INFO, WARN, or ERROR (uppercase).
- dimension should be pipeline_consistency unless a different dimension is clearly justified.
- Cover gaps between counts (e.g. attempted vs imported vs failed vs duplicates).
- If api_ingest_success is false, include at least one WARN or ERROR about API ingest.
"""


def _build_prompt_collection(*, run_id: str, standards_version: str) -> str:
    return f"""You are evaluating a DJ set COLLECTION UPDATE pipeline run against engineering standards v{standards_version}.

COLLECTION_UPDATE evaluation context:
- This run rebuilt the master DJ set collection spreadsheet and JSON snapshot.
- No CSV processing happened in this run.
- The Python job reached the evaluation step without raising an uncaught exception (treat as successful completion of the collection job unless counts or context imply otherwise).
- GitHub Actions run_id: {run_id}

Evaluate: did the collection update complete successfully?
- Dimension: pipeline_consistency
- If collection_update=True and there are no failures implied: emit an INFO finding confirming the collection was updated successfully.

Respond with ONLY valid JSON (no markdown) in this exact shape:
{{"findings":[{{"dimension":"pipeline_consistency","severity":"INFO|WARN|ERROR","finding":"...","suggestion":""}}]}}

Rules:
- severity must be INFO, WARN, or ERROR (uppercase).
"""


def evaluate_pipeline_run(
    *,
    run_id: str,
    repo: str,
    sets_imported: int,
    sets_failed: int,
    sets_skipped: int,
    total_tracks: int,
    failed_set_labels: list[str],
    api_ingest_success: bool,
    sets_attempted: int = 0,
    collection_update: bool = False,
    unrecognized_filename_skips: int = 0,
    duplicate_csv_count: int = 0,
) -> None:
    """
    Call Claude, then POST each finding to KAIANO_API_BASE_URL /v1/evaluations.
    Never raises — logs and returns on any failure.
    """
    if not os.environ.get("ANTHROPIC_API_KEY") or not os.environ.get(
        "KAIANO_API_BASE_URL"
    ):
        return

    standards_version = os.environ.get("STANDARDS_VERSION", "6.0")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    try:
        if collection_update:
            user_prompt = _build_prompt_collection(
                run_id=run_id, standards_version=standards_version
            )
        else:
            user_prompt = _build_prompt_csv(
                run_id=run_id,
                standards_version=standards_version,
                sets_imported=sets_imported,
                sets_failed=sets_failed,
                sets_skipped=sets_skipped,
                total_tracks=total_tracks,
                failed_set_labels=failed_set_labels,
                api_ingest_success=api_ingest_success,
                sets_attempted=sets_attempted,
                unrecognized_filename_skips=unrecognized_filename_skips,
                duplicate_csv_count=duplicate_csv_count,
            )

        text = _anthropic_messages_create(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            model=model,
            max_tokens=4096,
            user_prompt=user_prompt,
        )
        findings = _parse_findings_json(text)
    except Exception:
        log.exception("pipeline evaluation: Claude request or parse failed")
        return

    err_ct = warn_ct = info_ct = 0
    evaluated_at = datetime.now(UTC).isoformat()

    try:
        from kaiano.api import KaianoApiClient  # type: ignore[attr-defined]
        from kaiano.api.errors import KaianoApiError  # type: ignore[attr-defined]
    except Exception:
        log.exception("pipeline evaluation: Kaiano API client not available")
        return

    client = KaianoApiClient.from_env()

    for item in findings:
        if not isinstance(item, dict):
            continue
        dimension = str(item.get("dimension") or "pipeline_consistency")
        sev = str(item.get("severity") or "INFO").upper()
        if sev == "WARNING":
            sev = "WARN"
        if sev == "ERROR":
            err_ct += 1
        elif sev == "WARN":
            warn_ct += 1
        else:
            sev = "INFO"
            info_ct += 1
        finding = str(item.get("finding") or "")
        suggestion = str(item.get("suggestion") or "")
        details = {
            "run_id": run_id,
            "finding": finding,
            "suggestion": suggestion or None,
            "standards_version": standards_version,
            "evaluated_at": evaluated_at,
            "collection_update": collection_update,
        }
        payload = {
            "repo": repo,
            "dimension": dimension,
            "severity": sev,
            "details": details,
        }
        try:
            client.post("/v1/evaluations", payload)
        except KaianoApiError as e:
            log.warning("pipeline evaluation: failed to POST finding: %s", e)
        except Exception:
            log.exception("pipeline evaluation: unexpected error posting finding")

    log.info(
        "🤖 Evaluation complete: %d errors, %d warnings, %d info findings",
        err_ct,
        warn_ct,
        info_ct,
    )


def build_csv_evaluation_prompt(
    *,
    run_id: str,
    standards_version: str,
    sets_imported: int,
    sets_failed: int,
    sets_skipped: int,
    total_tracks: int,
    failed_set_labels: list[str],
    api_ingest_success: bool,
    sets_attempted: int,
    unrecognized_filename_skips: int = 0,
    duplicate_csv_count: int = 0,
) -> str:
    """Exposed for tests (same body as internal CSV prompt)."""
    return _build_prompt_csv(
        run_id=run_id,
        standards_version=standards_version,
        sets_imported=sets_imported,
        sets_failed=sets_failed,
        sets_skipped=sets_skipped,
        total_tracks=total_tracks,
        failed_set_labels=failed_set_labels,
        api_ingest_success=api_ingest_success,
        sets_attempted=sets_attempted,
        unrecognized_filename_skips=unrecognized_filename_skips,
        duplicate_csv_count=duplicate_csv_count,
    )


def build_collection_evaluation_prompt(*, run_id: str, standards_version: str) -> str:
    """Exposed for tests (same body as internal collection prompt)."""
    return _build_prompt_collection(run_id=run_id, standards_version=standards_version)
