"""CI 变更范围分类与汇总门禁校验；PR 与 main 推送共用同一套判定规则。"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import os
from pathlib import Path
import subprocess
import sys

ROOT_DOC_FILES = frozenset({"AGENTS.md", "README.md", "review.md"})
ZERO_SHA = "0" * 40
JOB_NAMES = ("docs", "python", "web", "formal", "process")


def is_doc_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or ".." in normalized.split("/"):
        return False
    if normalized in ROOT_DOC_FILES:
        return True
    if normalized.startswith(("docs/", ".github/ISSUE_TEMPLATE/")):
        return True
    return normalized.startswith((".claude/skills/", "formal/")) and normalized.endswith(".md")


def classify_paths(paths: Iterable[str]) -> str:
    items = [path for path in paths if path]
    if not items:
        return "full"
    return "docs" if all(is_doc_path(path) for path in items) else "full"


def _git(arguments: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _diff_paths(diff_range: list[str], *, cwd: Path) -> list[str] | None:
    # --no-renames reports both old and new paths so moving code into docs/ still triggers full CI.
    completed = _git(["diff", "--name-only", "-z", "--no-renames", *diff_range], cwd=cwd)
    if completed.returncode != 0:
        return None
    return [item for item in completed.stdout.split("\0") if item]


def changed_paths_for_event(
    event_name: str,
    *,
    base_sha: str | None = None,
    head_sha: str = "HEAD",
    cwd: Path | None = None,
) -> tuple[list[str] | None, str]:
    workdir = cwd or Path.cwd()
    if event_name == "pull_request":
        if _git(["rev-parse", "--verify", "--quiet", "HEAD^2"], cwd=workdir).returncode == 0:
            paths = _diff_paths(["HEAD^1", "HEAD"], cwd=workdir)
            if paths is not None:
                return paths, "pull_request merge diff (HEAD^1..HEAD)"
        if base_sha and base_sha != ZERO_SHA:
            paths = _diff_paths([f"{base_sha}...{head_sha}"], cwd=workdir)
            if paths is not None:
                return paths, f"pull_request diff ({base_sha[:12]}...{head_sha[:12]})"
        return None, "pull_request base unavailable"
    if event_name == "push":
        if not base_sha or base_sha == ZERO_SHA:
            return None, "push has no previous commit"
        if _git(["merge-base", "--is-ancestor", base_sha, head_sha], cwd=workdir).returncode != 0:
            return None, "push before commit is not an ancestor"
        paths = _diff_paths([base_sha, head_sha], cwd=workdir)
        if paths is None:
            return None, "push diff failed"
        return paths, f"push diff ({base_sha[:12]}..{head_sha[:12]})"
    return None, f"event {event_name or 'unknown'} always runs full"


def verify_results(scope_result: str, mode: str, results: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    if scope_result != "success":
        errors.append(f"scope result must be success, got {scope_result!r}")
    if mode == "docs":
        expected = {
            "docs": "success",
            "python": "skipped",
            "web": "skipped",
            "formal": "skipped",
            "process": "skipped",
        }
    elif mode == "full":
        expected = {
            "docs": "skipped",
            "python": "success",
            "web": "success",
            "formal": "success",
            "process": "success",
        }
    else:
        errors.append(f"scope mode must be 'docs' or 'full', got {mode!r}")
        return errors
    for name in JOB_NAMES:
        actual = results.get(name, "")
        if actual != expected[name]:
            errors.append(f"{name} expected {expected[name]!r}, got {actual!r}")
    return errors


def _write_key_value(path: Path, key: str, value: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify", help="判定本次 CI 执行范围")
    classify_parser.add_argument("--event", default=os.environ.get("EVENT_NAME", ""))
    classify_parser.add_argument("--base", default=os.environ.get("BASE_SHA", ""))
    classify_parser.add_argument("--head", default=os.environ.get("HEAD_SHA", "HEAD") or "HEAD")
    classify_parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    classify_parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))

    verify_parser = subparsers.add_parser("verify", help="校验汇总 CI 门禁")
    verify_parser.add_argument("--scope-result", default=os.environ.get("SCOPE_RESULT", ""))
    verify_parser.add_argument("--mode", default=os.environ.get("SCOPE_MODE", ""))
    for name in JOB_NAMES:
        verify_parser.add_argument(f"--{name}", default=os.environ.get(f"{name.upper()}_RESULT", ""))

    args = parser.parse_args(argv)
    if args.command == "classify":
        paths, reason = changed_paths_for_event(args.event, base_sha=args.base, head_sha=args.head)
        mode = classify_paths(paths) if paths is not None else "full"
        detail = f"{mode} ({len(paths)} changed paths; {reason})" if paths is not None else f"full (fallback: {reason})"
        print(f"CI scope: {detail}")
        if args.github_output:
            _write_key_value(Path(args.github_output), "mode", mode)
        if args.summary:
            with Path(args.summary).open("a", encoding="utf-8") as stream:
                stream.write(f"CI scope: `{detail}`\n")
        return 0

    results = {name: getattr(args, name) for name in JOB_NAMES}
    errors = verify_results(args.scope_result, args.mode, results)
    if errors:
        for error in errors:
            print(f"CI gate failed: {error}", file=sys.stderr)
        return 1
    print(f"CI gate passed for mode={args.mode}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
