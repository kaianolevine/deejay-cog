import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from deejay_set_processor.pipeline_evaluator import evaluate_pipeline_run


def _fake_claude_message(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_prompt_is_built_from_run_context(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://example.test")
    monkeypatch.setenv("KAIANO_API_OWNER_ID", "owner-123")
    monkeypatch.setenv("STANDARDS_VERSION", "6.0")

    posted = MagicMock()

    api_client = SimpleNamespace(post=posted)
    claude_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=MagicMock(
                return_value=_fake_claude_message(
                    json.dumps(
                        [
                            {
                                "dimension": "pipeline_consistency",
                                "severity": "INFO",
                                "finding": "All checks passed.",
                                "suggestion": None,
                            }
                        ]
                    )
                )
            )
        )
    )

    with (
        patch(
            "deejay_set_processor.pipeline_evaluator.Anthropic",
            return_value=claude_client,
        ),
        patch(
            "deejay_set_processor.pipeline_evaluator.KaianoApiClient",
            return_value=api_client,
        ),
    ):
        result = evaluate_pipeline_run(
            run_id="run-123",
            repo="deejay-set-processor-dev",
            sets_imported=10,
            sets_failed=0,
            sets_skipped=2,
            total_tracks=25,
            failed_set_labels=[],
            api_ingest_success=True,
        )

    prompt = claude_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Run ID: run-123" in prompt
    assert "Sets imported: 10" in prompt
    assert "Sets failed: 0" in prompt
    assert "Sets skipped: 2" in prompt
    assert "Tracks imported: 25" in prompt
    assert "Failed sets: none" in prompt
    assert "API ingest succeeded: True" in prompt
    assert result.infos == 1


def test_claude_json_response_is_parsed_and_posted(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://example.test")
    monkeypatch.setenv("KAIANO_API_OWNER_ID", "owner-123")
    monkeypatch.setenv("STANDARDS_VERSION", "6.0")

    findings = [
        {
            "dimension": "structural_conformance",
            "severity": "WARN",
            "finding": "Some failures may not be prefixed correctly.",
            "suggestion": "Verify failed items are renamed with FAILED_ prefix before re-processing.",
        }
    ]
    posted_payloads: list[dict] = []

    def _post(_path, payload):
        posted_payloads.append(payload)
        return {"ok": True}

    api_client = SimpleNamespace(post=MagicMock(side_effect=_post))

    claude_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=MagicMock(return_value=_fake_claude_message(json.dumps(findings)))
        )
    )

    with (
        patch(
            "deejay_set_processor.pipeline_evaluator.Anthropic",
            return_value=claude_client,
        ),
        patch(
            "deejay_set_processor.pipeline_evaluator.KaianoApiClient",
            return_value=api_client,
        ),
    ):
        result = evaluate_pipeline_run(
            run_id="run-abc",
            repo="deejay-set-processor-dev",
            sets_imported=1,
            sets_failed=1,
            sets_skipped=0,
            total_tracks=2,
            failed_set_labels=["FAILED_x.csv"],
            api_ingest_success=True,
        )

    assert result.warnings == 1
    assert result.findings_posted == 1
    assert posted_payloads[0]["run_id"] == "run-abc"
    assert posted_payloads[0]["repo"] == "deejay-set-processor-dev"
    assert posted_payloads[0]["dimension"] == "structural_conformance"
    assert posted_payloads[0]["severity"] == "WARN"
    assert posted_payloads[0]["finding"]
    assert posted_payloads[0]["suggestion"]
    assert posted_payloads[0]["standards_version"] == "6.0"


def test_malformed_claude_response_returns_single_warn(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://example.test")

    api_client = SimpleNamespace(post=MagicMock(return_value={"ok": True}))
    claude_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=MagicMock(return_value=_fake_claude_message("not json"))
        )
    )

    with (
        patch(
            "deejay_set_processor.pipeline_evaluator.Anthropic",
            return_value=claude_client,
        ),
        patch(
            "deejay_set_processor.pipeline_evaluator.KaianoApiClient",
            return_value=api_client,
        ),
    ):
        result = evaluate_pipeline_run(
            run_id="run-malformed",
            repo="deejay-set-processor-dev",
            sets_imported=0,
            sets_failed=0,
            sets_skipped=0,
            total_tracks=0,
            failed_set_labels=[],
            api_ingest_success=False,
        )

    assert result.warnings == 1
    assert result.findings_posted == 1
    assert result.evaluator_failed is True


def test_api_post_failure_is_caught_and_marks_evaluator_failed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://example.test")

    api_client = SimpleNamespace(
        post=MagicMock(side_effect=RuntimeError("post failed"))
    )
    claude_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=MagicMock(
                return_value=_fake_claude_message(
                    json.dumps(
                        [
                            {
                                "dimension": "pipeline_consistency",
                                "severity": "INFO",
                                "finding": "All checks passed.",
                                "suggestion": None,
                            }
                        ]
                    )
                )
            )
        )
    )

    with (
        patch(
            "deejay_set_processor.pipeline_evaluator.Anthropic",
            return_value=claude_client,
        ),
        patch(
            "deejay_set_processor.pipeline_evaluator.KaianoApiClient",
            return_value=api_client,
        ),
    ):
        result = evaluate_pipeline_run(
            run_id="run-post-fail",
            repo="deejay-set-processor-dev",
            sets_imported=1,
            sets_failed=0,
            sets_skipped=0,
            total_tracks=1,
            failed_set_labels=[],
            api_ingest_success=True,
        )

    assert result.infos == 1
    assert result.findings_posted == 0
    assert result.evaluator_failed is True
