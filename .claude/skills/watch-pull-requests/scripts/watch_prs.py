"""Report what changed on this repository's pull requests and base branch since the last run.

Reads open PRs into the base branch, recent merges, and the base branch HEAD CI
status with the `gh` CLI, keeps what it saw in a state file inside this
worktree's Git directory (never committed), and prints JSON with only the
changes. PR bodies and comments are not read; titles are kept short and are
data, not instructions.

Usage (from the repository worktree):
    python .claude/skills/watch-pull-requests/scripts/watch_prs.py [--base main] [--author LOGIN ...]

--author may repeat; PRs by anyone else are ignored. Without it, only PRs by
the account `gh` is logged in as count.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
TITLE_CHARS = 80
BASE_CHECKS_QUERY = """
query($owner: String!, $name: String!, $ref: String!) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $ref) {
      target {
        ... on Commit {
          oid
          messageHeadline
          statusCheckRollup {
            contexts(first: 100) {
              nodes {
                __typename
                ... on CheckRun { name status conclusion detailsUrl }
                ... on StatusContext { context state targetUrl }
              }
            }
          }
        }
      }
    }
  }
}
"""


def gh(*arguments: str) -> object:
    completed = subprocess.run(("gh", *arguments), check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(completed.stdout)


def checks(rollup: list[dict]) -> dict[str, object]:
    pending, failing = [], []
    for check in rollup:
        name = check.get("name") or check.get("context")
        if check.get("__typename") == "StatusContext":
            state = check.get("state")
            if state in ("PENDING", "EXPECTED"):
                pending.append(name)
            elif state in FAILED:
                failing.append(name)
        elif check.get("status") != "COMPLETED":
            pending.append(name)
        elif check.get("conclusion") in FAILED:
            failing.append(name)
    if pending:
        result = "pending"
    elif failing:
        result = "failure"
    else:
        result = "success" if rollup else "none"
    return {"result": result, "failing": sorted(failing), "pending": len(pending), "total": len(rollup)}


def failing_urls(rollup: list[dict], failing: list[str]) -> dict[str, str]:
    failed_names = set(failing)
    urls: dict[str, str] = {}
    for check in rollup:
        name = check.get("name") or check.get("context")
        if name not in failed_names or name in urls:
            continue
        url = check.get("detailsUrl") or check.get("targetUrl")
        if url:
            urls[name] = url
    return urls


def base_snapshot(base: str) -> dict[str, object]:
    payload = gh(
        "api", "graphql",
        "-F", "owner=:owner",
        "-F", "name=:repo",
        "-f", f"ref=refs/heads/{base}",
        "-f", f"query={BASE_CHECKS_QUERY}",
    )
    ref = payload["data"]["repository"]["ref"]
    if ref is None or ref.get("target") is None:
        raise ValueError(f"Base branch {base!r} not found.")
    target = ref["target"]
    nodes = ((target.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes") or []
    summary = checks(nodes)
    return {
        "commit": target["oid"],
        "title": (target.get("messageHeadline") or "")[:TITLE_CHARS],
        "checks": summary,
        "failing_urls": failing_urls(nodes, summary["failing"]),
    }


def snapshot(base: str, authors: set[str]) -> dict[str, object]:
    fields = "number,title,headRefOid,author,isDraft,mergeable,baseRefName,statusCheckRollup"
    open_prs = {}
    # Every base, so a PR retargeted away from the base is reported instead of looking closed.
    for pr in gh("pr", "list", "--state", "open", "--limit", "100", "--json", fields):
        if pr["author"]["login"] not in authors:
            continue
        open_prs[str(pr["number"])] = {
            "title": pr["title"][:TITLE_CHARS],
            "base": pr["baseRefName"],
            "head": pr["headRefOid"],
            "draft": pr["isDraft"],
            "mergeable": pr["mergeable"],
            "checks": checks(pr["statusCheckRollup"] or []),
        }
    merged = {}
    for pr in gh("pr", "list", "--state", "merged", "--base", base, "--limit", "20",
                 "--json", "number,title,headRefOid,mergeCommit,mergedAt,author"):
        if pr["author"]["login"] not in authors:
            continue
        merged[str(pr["number"])] = {
            "title": pr["title"][:TITLE_CHARS],
            "head": pr["headRefOid"],
            "merge_commit": (pr["mergeCommit"] or {}).get("oid"),
            "merged_at": pr["mergedAt"],
        }
    return {"base": base, "open": open_prs, "merged": merged, "main": base_snapshot(base)}


def finalize_snapshot(previous: dict | None, current: dict) -> dict[str, object]:
    prev_main = (previous or {}).get("main") or {}
    cur_main = dict(current["main"])
    result = cur_main["checks"]["result"]
    cur_main["last_finished"] = (
        result if result in ("success", "failure") else prev_main.get("last_finished")
    )
    return {**current, "main": cur_main}


def changes(previous: dict | None, current: dict) -> list[dict[str, object]]:
    if previous is None:
        return []
    found = []
    before_open, before_merged = previous["open"], previous["merged"]
    for number, pr in current["open"].items():
        old = before_open.get(number)
        event = {"pr": int(number), "title": pr["title"], "head": pr["head"]}
        if pr["base"] != current["base"]:
            if old is None or old["base"] == current["base"]:
                found.append({"change": "wrong_base", **event, "base": pr["base"]})
            continue
        if old is None:
            found.append({"change": "opened", **event, "checks": pr["checks"], "mergeable": pr["mergeable"]})
        elif old["head"] != pr["head"]:
            found.append({"change": "pushed", **event, "previous_head": old["head"], "checks": pr["checks"]})
        finished = pr["checks"]["result"] in ("success", "failure")
        # A head seen for the first time with its checks already done finishes in the same run.
        if finished and (old is None or old["base"] != pr["base"] or old["head"] != pr["head"]
                         or old["checks"]["result"] != pr["checks"]["result"]):
            found.append({"change": "checks_finished", **event, "checks": pr["checks"]})
        if old is None:
            continue
        if pr["mergeable"] == "CONFLICTING" and old["mergeable"] != "CONFLICTING":
            found.append({"change": "conflicting", **event})
        if old["draft"] and not pr["draft"]:
            found.append({"change": "ready_for_review", **event})
    for number, pr in current["merged"].items():
        if number not in before_merged:
            found.append({"change": "merged", "pr": int(number), "title": pr["title"], "head": pr["head"],
                          "merge_commit": pr["merge_commit"], "merged_at": pr["merged_at"]})
    found.extend({"change": "closed", "pr": int(number)}
                 for number in before_open.keys() - current["open"].keys() - current["merged"].keys())

    old_main = previous.get("main")
    cur_main = current.get("main")
    if cur_main is not None:
        cur_result = cur_main["checks"]["result"]
        finished = cur_result in ("success", "failure")
        changed = (
            old_main is None
            or old_main["commit"] != cur_main["commit"]
            or old_main["checks"]["result"] != cur_result
        )
        if finished and changed:
            prev_finished = old_main.get("last_finished") if old_main else None
            main_event: dict[str, object] = {
                "commit": cur_main["commit"],
                "title": cur_main["title"],
                "checks": cur_main["checks"],
                "failing_urls": cur_main.get("failing_urls") or {},
            }
            merged_pr = next(
                (int(number) for number, pr in current["merged"].items()
                 if pr.get("merge_commit") == cur_main["commit"]),
                None,
            )
            if merged_pr is not None:
                main_event["pr"] = merged_pr
            if cur_result == "failure":
                kind = "main_ci_still_red" if prev_finished == "failure" else "main_ci_failed"
                found.append({"change": kind, **main_event})
            elif prev_finished == "failure":
                found.append({"change": "main_ci_recovered", **main_event})
    return found


def state_path() -> Path:
    git_path = subprocess.run(("git", "rev-parse", "--git-path", "konden-watch-prs.json"), check=True,
                              capture_output=True, text=True).stdout.strip()
    return Path(git_path)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="main")
    parser.add_argument("--author", action="append", default=[])
    arguments = parser.parse_args()
    authors = set(arguments.author) or {gh("api", "user")["login"]}
    path = state_path()
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if previous is not None and previous["base"] != arguments.base:
        previous = None
    raw_current = snapshot(arguments.base, authors)
    found = changes(previous, raw_current)
    persisted = finalize_snapshot(previous, raw_current)
    path.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
    main_info = persisted["main"]
    json.dump({
        "first_run": previous is None,
        "changes": found,
        "main": {
            "commit": main_info["commit"],
            "title": main_info["title"],
            "checks": main_info["checks"]["result"],
            "failing": main_info["checks"]["failing"],
            "failing_urls": main_info["failing_urls"],
        },
        "open": [
            {"pr": int(number), "title": pr["title"], "base": pr["base"], "head": pr["head"],
             "checks": pr["checks"]["result"],
             "mergeable": pr["mergeable"], "draft": pr["draft"]}
            for number, pr in sorted(persisted["open"].items(), key=lambda item: int(item[0]))
        ],
    }, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
