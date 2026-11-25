from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / ".claude/skills/watch-pull-requests/scripts/watch_prs.py"
SPEC = importlib.util.spec_from_file_location("watch_prs", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
watch_prs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch_prs)


def _state(commit: str, result: str, *, failing: list[str] | None = None,
           urls: dict[str, str] | None = None, last_finished: str | None = None,
           merged: dict | None = None) -> dict:
    return {
        "base": "main",
        "open": {},
        "merged": merged or {},
        "main": {
            "commit": commit,
            "title": f"commit {commit}",
            "checks": {
                "result": result,
                "failing": failing or [],
                "pending": 1 if result == "pending" else 0,
                "total": 1,
            },
            "failing_urls": urls or {},
            **({"last_finished": last_finished} if last_finished is not None else {}),
        },
    }


def test_main_ci_red_still_red_and_recovery_lifecycle():
    """Main CI reports failure once, flags continued red across pending runs, and reports recovery."""
    # Baseline: green on c1; next green commit c2 stays silent.
    s1 = watch_prs.finalize_snapshot(None, _state("c1", "success"))
    s2_raw = _state("c2", "success")
    assert watch_prs.changes(s1, s2_raw) == []
    s2 = watch_prs.finalize_snapshot(s1, s2_raw)

    # c3 fails -> main_ci_failed with linked PR and failure URL; polling again stays silent.
    merged_c3 = {"42": {"title": "feat", "head": "h42", "merge_commit": "c3", "merged_at": "2026-11-25T00:00:00Z"}}
    s3_raw = _state("c3", "failure", failing=["CI", "Web"], urls={"Web": "https://example.test/job/1"}, merged=merged_c3)
    events = watch_prs.changes(s2, s3_raw)
    assert [e["change"] for e in events] == ["merged", "main_ci_failed"]
    fail_event = events[1]
    assert fail_event["commit"] == "c3"
    assert fail_event["pr"] == 42
    assert fail_event["failing_urls"] == {"Web": "https://example.test/job/1"}

    s3 = watch_prs.finalize_snapshot(s2, s3_raw)
    assert watch_prs.changes(s3, s3_raw) == []

    # c4 is pending while main is still red -> preserves last_finished="failure", emits no noise yet.
    s4_pending_raw = _state("c4", "pending", merged=merged_c3)
    assert watch_prs.changes(s3, s4_pending_raw) == []
    s4_pending = watch_prs.finalize_snapshot(s3, s4_pending_raw)
    assert s4_pending["main"]["last_finished"] == "failure"

    # c4 also fails -> main_ci_still_red.
    s4_fail_raw = _state("c4", "failure", failing=["CI"], merged=merged_c3)
    assert [e["change"] for e in watch_prs.changes(s4_pending, s4_fail_raw)] == ["main_ci_still_red"]
    s4_fail = watch_prs.finalize_snapshot(s4_pending, s4_fail_raw)

    # c5 goes pending then succeeds -> main_ci_recovered.
    s5_pending_raw = _state("c5", "pending", merged=merged_c3)
    assert watch_prs.changes(s4_fail, s5_pending_raw) == []
    s5_pending = watch_prs.finalize_snapshot(s4_fail, s5_pending_raw)

    s5_green_raw = _state("c5", "success", merged=merged_c3)
    recovered = watch_prs.changes(s5_pending, s5_green_raw)
    assert [e["change"] for e in recovered] == ["main_ci_recovered"]
    assert recovered[0]["commit"] == "c5"


def test_main_ci_legacy_state_file_without_main_field_reports_current_red_once():
    """Upgrading from an older state file without 'main' still alerts if main is currently failing."""
    legacy_previous = {"base": "main", "open": {}, "merged": {}}
    failing_now = _state("c9", "failure", failing=["CI"])
    assert [e["change"] for e in watch_prs.changes(legacy_previous, failing_now)] == ["main_ci_failed"]

    green_now = _state("c9", "success")
    assert watch_prs.changes(legacy_previous, green_now) == []
