"""Codex project discovery and matching local chats to project directories."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from pydantic import BaseModel, ConfigDict, Field, field_validator

from konden.assistant.host.codex_app_server import CodexRequestError
from konden.assistant.host.codex_titles import ThreadSummary


class CodexProjectUnavailable(ValueError):
    pass


class CodexProjectTarget(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str = Field(min_length=1)
    root: str | None = Field(default=None, min_length=1)


class ProjectRoot(BaseModel):
    model_config = ConfigDict(strict=True)
    path: str

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("Codex project roots must be absolute")
        return value


class CodexProject(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    roots: list[ProjectRoot]

    def working_directory(self, selected: str | None) -> Path:
        roots = [root.path for root in self.roots]
        if selected is None and len(roots) == 1:
            selected = roots[0]
        if selected is None or selected not in roots:
            raise CodexProjectUnavailable("请选择此 Codex 工作区登记的目录。")
        directory = Path(selected)
        if not directory.is_dir():
            raise CodexProjectUnavailable("此 Codex 工作区的目录不可用，请在 Codex 中检查工作区。")
        return directory


async def read_codex_project(server, project_id: str) -> CodexProject:
    try:
        result = await server.request("project/read", {"projectId": project_id})
    except CodexRequestError as error:
        if error.error == {"code": -32602, "message": f"project not found: {project_id}"}:
            raise CodexProjectUnavailable("找不到这个 Codex 工作区，请重新选择。") from error
        raise
    project = CodexProject.model_validate(result["project"])
    if project.id != project_id:
        raise ValueError("Codex replied with a different project id")
    return project


async def list_codex_projects(server) -> list[CodexProject] | None:
    projects = []
    params = {"limit": 100}
    while True:
        try:
            result = await server.request("project/list", params)
        except CodexRequestError as error:
            if "cursor" not in params and (
                error.error["code"] == -32601
                or (error.error["code"] == -32600 and "unknown variant `project/list`" in error.error["message"])
            ):
                return None
            raise
        projects.extend(CodexProject.model_validate(item) for item in result["data"])
        if result["nextCursor"] is None:
            return projects
        params["cursor"] = result["nextCursor"]


def _worktree_roots(git: str, root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    result = subprocess.run(
        [git, "-C", str(root), "worktree", "list", "--porcelain", "-z"],
        env={**environment, "LC_ALL": "C"}, capture_output=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode == 128 and result.stderr.startswith(b"fatal: not a git repository"):
        return []
    result.check_returncode()
    return [Path(os.fsdecode(field.removeprefix(b"worktree "))).resolve()
            for field in result.stdout.split(b"\0") if field.startswith(b"worktree ")]


def _project_at(path: Path, roots: list[tuple[Path, str]]) -> set[str]:
    matches = [(len(root.parts), project) for root, project in roots if path.is_relative_to(root)]
    depth = max((length for length, _ in matches), default=0)
    return {project for length, project in matches if length == depth}


def assign_chat_projects(projects: list[CodexProject], threads: dict[str, ThreadSummary]) -> dict[str, str]:
    project_ids = {project.id for project in projects}
    roots = [(Path(root.path).resolve(), project.id) for project in projects for root in project.roots]
    assigned = {}
    unmatched = {}
    for identity, thread in threads.items():
        if thread.project_id is not None:
            if thread.project_id in project_ids:
                assigned[identity] = thread.project_id
        elif thread.cwd is not None and Path(thread.cwd).is_absolute():
            directory = Path(thread.cwd).resolve()
            matches = _project_at(directory, roots)
            if len(matches) == 1:
                assigned[identity] = next(iter(matches))
            elif not matches:
                unmatched[identity] = directory

    git = shutil.which("git") if roots and unmatched else None
    if git is not None:
        worktrees = {root: _worktree_roots(git, root) for root in dict.fromkeys(path for path, _ in roots)}
        related = [(worktree, project) for root, project in roots for worktree in worktrees[root]]
        for identity, directory in unmatched.items():
            matches = _project_at(directory, related)
            if len(matches) == 1:
                assigned[identity] = next(iter(matches))
    return assigned
