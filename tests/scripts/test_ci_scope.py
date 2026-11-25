from __future__ import annotations

import itertools
from pathlib import Path
import subprocess

import pytest

from scripts import ci_scope


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _init_repo(repo: Path) -> str:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "konden").mkdir()
    (repo / "docs").mkdir()
    (repo / "konden" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "docs" / "指南.md").write_text("# 指南\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("path,expected", [
    ("README.md", True),
    ("AGENTS.md", True),
    ("review.md", True),
    ("docs/开发流程.md", True),
    ("docs/架构/图.svg", True),
    (".github/ISSUE_TEMPLATE/缺陷.md", True),
    (".claude/skills/verify-konden/features/setup-and-launch.md", True),
    ("formal/01-CommandRecovery/README.md", True),
    (".claude/skills/watch-pull-requests/scripts/watch_prs.py", False),
    ("formal/01-CommandRecovery/CommandRecovery.tla", False),
    ("formal/toolchain.json", False),
    (".github/workflows/ci.yml", False),
    ("konden/skills/bundled/independent-verification/SKILL.md", False),
    ("web/tests/markdownLinks.test.ts", False),
    ("scripts/ci.py", False),
    ("docs/../konden/app.py", False),
    ("", False),
])
def test_doc_path_allowlist_separates_docs_from_executable_or_config_files(path, expected):
    """Only documentation files qualify for docs-only CI; scripts, models, and bundled skills must run full CI."""
    assert ci_scope.is_doc_path(path) is expected


def test_classify_paths_defaults_empty_or_mixed_changes_to_full():
    """Empty diffs or mixed doc+code changes must never downgrade to docs-only checks."""
    assert ci_scope.classify_paths([]) == "full"
    assert ci_scope.classify_paths(["docs/开发流程.md", "README.md"]) == "docs"
    assert ci_scope.classify_paths(["docs/开发流程.md", "scripts/ci.py"]) == "full"


def test_pull_request_merge_commit_classifies_chinese_doc_changes_and_blocks_rename_escape(tmp_path):
    """PR classification inspects the actual merge diff and catches renaming code into docs/."""
    base = _init_repo(tmp_path)

    # Pure docs PR merge commit with a Chinese filename.
    _git(tmp_path, "checkout", "-b", "docs-pr", base)
    (tmp_path / "docs" / "指南.md").write_text("# 更新\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "update doc")
    doc_head = _git(tmp_path, "rev-parse", "HEAD")

    _git(tmp_path, "checkout", "--detach", base)
    _git(tmp_path, "merge", "--no-ff", doc_head, "-m", "merge docs pr")
    paths, _ = ci_scope.changed_paths_for_event("pull_request", base_sha=base, cwd=tmp_path)
    assert paths == ["docs/指南.md"]
    assert ci_scope.classify_paths(paths) == "docs"

    # Moving a Python file into docs/ must still be classified as full CI.
    _git(tmp_path, "checkout", "-b", "rename-pr", base)
    _git(tmp_path, "mv", "konden/app.py", "docs/app.md")
    _git(tmp_path, "commit", "-m", "move code into docs")
    rename_head = _git(tmp_path, "rev-parse", "HEAD")

    _git(tmp_path, "checkout", "--detach", base)
    _git(tmp_path, "merge", "--no-ff", rename_head, "-m", "merge rename pr")
    paths, _ = ci_scope.changed_paths_for_event("pull_request", base_sha=base, cwd=tmp_path)
    assert set(paths) == {"konden/app.py", "docs/app.md"}
    assert ci_scope.classify_paths(paths) == "full"


def test_push_classification_handles_docs_push_and_falls_back_on_missing_or_rewritten_base(tmp_path):
    """Push events share the same docs rule, while unknown or force-pushed bases fall back to full CI."""
    base = _init_repo(tmp_path)
    (tmp_path / "docs" / "指南.md").write_text("# 新版\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "docs push")
    after = _git(tmp_path, "rev-parse", "HEAD")

    paths, _ = ci_scope.changed_paths_for_event("push", base_sha=base, head_sha=after, cwd=tmp_path)
    assert paths == ["docs/指南.md"]
    assert ci_scope.classify_paths(paths) == "docs"

    # Zero SHA (initial branch push) and non-ancestor SHA must fall back to full.
    fallback_zero, _ = ci_scope.changed_paths_for_event("push", base_sha=ci_scope.ZERO_SHA, head_sha=after, cwd=tmp_path)
    assert fallback_zero is None

    _git(tmp_path, "checkout", "--orphan", "rewritten")
    (tmp_path / "docs" / "指南.md").write_text("# 孤立分支\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "orphan")
    orphan = _git(tmp_path, "rev-parse", "HEAD")
    fallback_force, _ = ci_scope.changed_paths_for_event("push", base_sha=base, head_sha=orphan, cwd=tmp_path)
    assert fallback_force is None

    dispatch_paths, _ = ci_scope.changed_paths_for_event("workflow_dispatch", base_sha=base, head_sha=after, cwd=tmp_path)
    assert dispatch_paths is None


def test_verify_results_accepts_only_exact_expected_matrix_for_each_mode():
    """The CI aggregate gate must reject any failure, cancellation, unexpected skip, or broken scope."""
    states = ("success", "failure", "cancelled", "skipped")
    for mode in ("docs", "full"):
        expected = (
            {"docs": "success", "python": "skipped", "web": "skipped", "formal": "skipped", "process": "skipped"}
            if mode == "docs"
            else {"docs": "skipped", "python": "success", "web": "success", "formal": "success", "process": "success"}
        )
        passing = []
        for combo in itertools.product(states, repeat=len(ci_scope.JOB_NAMES)):
            results = dict(zip(ci_scope.JOB_NAMES, combo, strict=True))
            if not ci_scope.verify_results("success", mode, results):
                passing.append(results)
        assert passing == [expected]

    assert ci_scope.verify_results("failure", "docs", {
        "docs": "success", "python": "skipped", "web": "skipped", "formal": "skipped", "process": "skipped",
    })
    assert ci_scope.verify_results("success", "", {
        "docs": "skipped", "python": "success", "web": "success", "formal": "success", "process": "success",
    })
