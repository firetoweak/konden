"""Prevent access settings from diverging across files, command cwd and children."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from konden.assistant.agents import AgentCreated, fact_arguments
from konden.assistant.builtin_tools import bind_files, build_builtin_tools
from konden.assistant.host.file_access import DirectoryAccess, FullAccess, FileAccessBusy, FileAccessInvalid
from konden.assistant.host.session_store import SessionStore
from konden.assistant.host.supervisor import HostSupervisor
from konden.assistant.subagent.subagent import DelegateIntent, task_fact_arguments
from konden.config import AssistantConfig
from konden.filesystem.workspace import FileWorkspace
from konden.execution.api import CommandEnvironment
from konden.execution.command import CapturedOutput, CommandResult
from konden.tools.builtin.command_execution import CommandInput, create_command_execution_spec
from konden.paths import KondenHome
from konden.runtime import AgentRuntime, ModelDecision


async def _saved_access_is_current_after_restart_and_inherited_by_children(tmp_path):
    home = KondenHome(tmp_path / "home")
    store = SessionStore(home.runtime_sessions_root)
    own, outside = tmp_path / "own", tmp_path / "outside"
    own.mkdir()
    outside.mkdir()
    host = HostSupervisor(store, AssistantConfig, home, None, llm=None)
    try:
        await store.create("bot", directory=own,
                           initial_fact=fact_arguments(AgentCreated(name="Bot", directory=str(own)), delivery_id="created"))
        runtime = AgentRuntime(store.journal("bot"), SimpleNamespace(
            decide=AsyncMock(return_value=ModelDecision(content="ready"))), {})
        await runtime.advance("bot")
        assert (await host.file_access.get("bot")).model_dump() == {"mode": "directories", "directories": []}
        configured = await host.file_access.save("bot", DirectoryAccess(directories=[str(outside), str(outside / ".")]))
        assert configured.directories == [str(outside.resolve())]
        await store.create("bot/sub-child", directory=own, initial_fact=task_fact_arguments(
            DelegateIntent("child", "bot", "bot/sub-child", "inspect outside")))
        events = await store.journal("bot/sub-child").snapshot("bot/sub-child")
        workspace = await host.file_access.workspace("bot/sub-child", events)
        assert workspace.directories == (outside.resolve(),)
        # The current settings, rather than the child's creation time, determine its access.
        await host.file_access.save("bot", FullAccess(mode="full"))
        assert (await host.file_access.workspace("bot/sub-child", events)).full_access
    finally:
        await host.close()
    restarted = HostSupervisor(store, AssistantConfig, home, None, llm=None)
    try:
        assert (await restarted.file_access.get("bot")).mode == "full"
        await restarted.file_access.save("bot", DirectoryAccess())
        workspace = await restarted.file_access.workspace("bot/sub-child", events)
        assert not workspace.full_access and workspace.directories == ()
        restarted.file_access.path("bot").write_text('{"mode":"invalid"}', encoding="utf-8")
        with pytest.raises(ValidationError):
            await restarted.file_access.get("bot")
    finally:
        await restarted.close()


async def _file_scope_keeps_own_directory_writable_and_materials_and_colleagues_read_only(tmp_path, full):
    own, outside, colleagues, materials = (tmp_path / name for name in ("own", "outside", "colleagues", "materials"))
    for directory in (own, outside, colleagues, materials):
        directory.mkdir()
    nested = (colleagues / "other-bot", materials / "nested")
    for directory in nested:
        directory.mkdir()
    (colleagues / "note.txt").write_text("colleague", encoding="utf-8")
    workspace = FileWorkspace(own, full_access=full,
                              directories=() if full else (outside, colleagues, materials, *nested))
    tools = await build_builtin_tools(workspace, materials_root=materials, colleague_roots=(colleagues,))
    for path in (own / "own.txt", outside / "external.txt"):
        result = await tools.execute("write_file", {"path": str(path), "content": "allowed"})
        assert result["ok"] and path.read_text() == "allowed"
    for path in (colleagues / "note.txt", materials / "original.txt", *(directory / "blocked.txt" for directory in nested)):
        result = await tools.execute("write_file", {"path": str(path), "content": "blocked"})
        assert result["code"] == "ENVIRONMENT_PERMISSION_DENIED"
    assert (colleagues / "note.txt").read_text() == "colleague"
    # The same grants must permit cwd, and explicit read-only roots must reject it.
    empty = CapturedOutput("", 0, False, 0)
    runner = SimpleNamespace(run=AsyncMock(return_value=CommandResult(0, empty, empty, 1, False)))
    binding = bind_files(workspace, materials_root=materials, colleague_roots=(colleagues,))
    command = create_command_execution_spec(binding, CommandEnvironment(runner, "test", "test"))
    assert (await command.handler(CommandInput(command="test", cwd=str(outside))))["ok"]
    assert runner.run.call_args.args[1] == outside.resolve()
    for directory in (materials, colleagues, *nested):
        assert (await command.handler(CommandInput(command="test", cwd=str(directory))))["code"] == "ENVIRONMENT_PERMISSION_DENIED"


async def _invalid_and_busy_changes_leave_the_saved_policy_unchanged(tmp_path):
    home = KondenHome(tmp_path / "home")
    own = tmp_path / "own"
    own.mkdir()
    host = HostSupervisor(SessionStore(home.runtime_sessions_root), AssistantConfig, home, None, llm=None)
    try:
        await host.store.create("bot", directory=own,
                               initial_fact=fact_arguments(AgentCreated(name="Bot", directory=str(own)), delivery_id="created"))
        with pytest.raises(FileAccessInvalid):
            await host.file_access.save("bot", DirectoryAccess(directories=["relative"]))
        # An unprocessed request is already work even before a Worker advertises busy.
        with pytest.raises(FileAccessBusy):
            await host.file_access.save("bot", FullAccess(mode="full"))
        runtime = AgentRuntime(host.store.journal("bot"), SimpleNamespace(
            decide=AsyncMock(return_value=ModelDecision(content="ready"))), {})
        await runtime.advance("bot")
        await host.file_access.save("bot", FullAccess(mode="full"))
        colleague = home.agent_directories_root / "other-bot" / "nested"
        colleague.mkdir(parents=True)
        child_id = "bot/sub-readonly"
        result = await host._route("create_child", child_id, task_fact_arguments(
            DelegateIntent("readonly", "bot", child_id, "inspect colleague", cwd=str(colleague))))
        assert result["code"] == "ENVIRONMENT_PERMISSION_DENIED"
        assert not host.store.path(child_id).parent.exists()
        await host.file_access.save("bot", DirectoryAccess())
        host.commands.manager.list = AsyncMock(return_value=[{"state": "running"}])
        with pytest.raises(FileAccessBusy, match="后台命令"):
            await host.file_access.save("bot", FullAccess(mode="full"))
        assert (await host.file_access.get("bot")).mode == "directories"
    finally:
        await host.close()


def test_saved_access_is_current_after_restart_and_inherited_by_children(tmp_path):
    asyncio.run(_saved_access_is_current_after_restart_and_inherited_by_children(tmp_path))


@pytest.mark.parametrize("full", [False, True])
def test_file_scope_keeps_own_directory_writable_and_materials_and_colleagues_read_only(tmp_path, full):
    asyncio.run(_file_scope_keeps_own_directory_writable_and_materials_and_colleagues_read_only(tmp_path, full))


def test_invalid_and_busy_changes_leave_the_saved_policy_unchanged(tmp_path):
    asyncio.run(_invalid_and_busy_changes_leave_the_saved_policy_unchanged(tmp_path))
