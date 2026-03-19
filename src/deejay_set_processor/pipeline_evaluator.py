from __future__ import annotations

import dataclasses
import json
import os
from typing import Any

try:
    from anthropic import Anthropic  # type: ignore
except Exception:  # pragma: no cover
    Anthropic = None  # type: ignore[assignment]

try:
    from kaiano.api import KaianoApiClient  # type: ignore
except Exception:  # pragma: no cover
    KaianoApiClient = None  # type: ignore[assignment]

try:
    from kaiano import logger as logger_mod  # type: ignore

    log = logger_mod.get_logger()
except Exception:  # pragma: no cover
    import logging

    log = logging.getLogger(__name__)


@dataclasses.dataclass
class EvaluationResult:
    findings_posted: int
    errors: int
    warnings: int
    infos: int
    evaluator_failed: bool = False


def _build_claude_prompt(
    *,
    run_id: str,
    sets_imported: int,
    sets_failed: int,
    sets_skipped: int,
    total_tracks: int,
    failed_set_labels: list[str],
    api_ingest_success: bool,
) -> str:
    failed = failed_set_labels or ["none"]

    # Keep rubric text explicit and parse-friendly for Claude.
    return f"""
Repo: deejay-set-processor-dev
Run ID: {run_id}
Sets imported: {sets_imported}
Sets failed: {sets_failed}
Sets skipped: {sets_skipped}
Tracks imported: {total_tracks}
Failed sets: {", ".join(failed) or "none"}
API ingest succeeded: {api_ingest_success}

Evaluate this pipeline run against these standards:

PIPELINE_CONSISTENCY: Did the run behave as expected?
- Any sets failed? (ERROR if failed > 0 with no known reason)
- Were skipped sets accounted for by idempotency? (INFO)
- Did API ingest succeed? (ERROR if False)

STRUCTURAL_CONFORMANCE: Does the pipeline follow patterns?
- Pipeline continued on per-item failures (PRINCIPLE)
- Failed items were prefixed FAILED_ (PATTERN)
- Archive, never delete (PATTERN)

Respond ONLY with a JSON array of findings. Each finding:
{{
  "dimension": "pipeline_consistency|structural_conformance",
  "severity": "ERROR|WARN|INFO",
  "finding": "One sentence describing what was observed.",
  "suggestion": "One sentence concrete remediation or null."
}}

If everything looks good, return one INFO finding confirming it.
""".strip()


def _extract_text_from_claude_message(message: Any) -> str:
    # Anthropic SDK typically returns: message.content = [ContentBlock(text=...)]
    content = getattr(message, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        text = getattr(first, "text", None)
        if text:
            return str(text)
    return str(message)


def _parse_findings_from_claude(text: str) -> tuple[list[dict[str, Any]], bool]:
    """
    Returns (findings, evaluator_failed_flag).
    """
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Claude response was not a JSON array.")

        # Minimal shape validation.
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("Finding items must be objects.")
            if (
                "dimension" not in item
                or "severity" not in item
                or "finding" not in item
            ):
                raise ValueError("Finding missing required fields.")

        return parsed, False
    except Exception as e:
        log.error("Failed to parse Claude response as JSON: %s", e)
        return [
            {
                "dimension": "pipeline_consistency",
                "severity": "WARN",
                "finding": "Evaluator failed to parse Claude response; returning a warning-only result.",
                "suggestion": None,
            }
        ], True


def evaluate_pipeline_run(
    run_id: str,
    repo: str,
    sets_imported: int,
    sets_failed: int,
    sets_skipped: int,
    total_tracks: int,
    failed_set_labels: list[str],
    api_ingest_success: bool,
) -> EvaluationResult:
    """
    Call Claude to evaluate this pipeline run against the standards.
    Post findings to deejay-marvel-api.
    """
    standards_version = (os.getenv("STANDARDS_VERSION") or "6.0").strip()
    api_base_url = (os.getenv("KAIANO_API_BASE_URL") or "").strip()
    owner_id = (os.getenv("KAIANO_API_OWNER_ID") or "dev-owner").strip()
    anthropic_api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()

    if not anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY missing; skipping evaluation.")
        return EvaluationResult(
            findings_posted=0, errors=0, warnings=0, infos=0, evaluator_failed=True
        )
    if not api_base_url:
        log.warning("KAIANO_API_BASE_URL missing; skipping evaluation.")
        return EvaluationResult(
            findings_posted=0, errors=0, warnings=0, infos=0, evaluator_failed=True
        )

    prompt = _build_claude_prompt(
        run_id=run_id,
        sets_imported=sets_imported,
        sets_failed=sets_failed,
        sets_skipped=sets_skipped,
        total_tracks=total_tracks,
        failed_set_labels=failed_set_labels,
        api_ingest_success=api_ingest_success,
    )

    evaluator_failed = False
    findings: list[dict[str, Any]]

    # --- Calling Claude -------------------------------------------------------
    try:
        if Anthropic is None:  # pragma: no cover
            raise RuntimeError("Anthropic SDK not available.")

        client = Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        claude_text = _extract_text_from_claude_message(message)
        findings, evaluator_failed = _parse_findings_from_claude(claude_text)
    except Exception as e:  # pragma: no cover
        log.error("Claude evaluation call failed: %s", e)
        evaluator_failed = True
        findings = [
            {
                "dimension": "pipeline_consistency",
                "severity": "WARN",
                "finding": "Evaluator failed during Claude call; returning a warning-only result.",
                "suggestion": None,
            }
        ]

    # --- Counting findings ----------------------------------------------------
    errors = sum(1 for f in findings if f.get("severity") == "ERROR")
    warnings = sum(1 for f in findings if f.get("severity") == "WARN")
    infos = sum(1 for f in findings if f.get("severity") == "INFO")

    # --- Posting findings -----------------------------------------------------
    if KaianoApiClient is None:  # pragma: no cover
        log.error("KaianoApiClient not available; cannot post findings.")
        return EvaluationResult(
            findings_posted=0,
            errors=errors,
            warnings=warnings,
            infos=infos,
            evaluator_failed=True,
        )

    api_client = KaianoApiClient(base_url=api_base_url, owner_id=owner_id)

    findings_posted = 0
    for f in findings:
        payload = {
            "run_id": run_id,
            "repo": repo,
            "dimension": f.get("dimension"),
            "severity": f.get("severity"),
            "finding": f.get("finding"),
            "suggestion": f.get("suggestion"),
            "standards_version": standards_version,
        }
        try:
            api_client.post("/v1/evaluations", payload)
            findings_posted += 1
        except Exception as e:
            # Evaluation failures must never abort the pipeline.
            log.error("Failed to post evaluation finding: %s", e)
            evaluator_failed = True

    return EvaluationResult(
        findings_posted=findings_posted,
        errors=errors,
        warnings=warnings,
        infos=infos,
        evaluator_failed=evaluator_failed,
    )
