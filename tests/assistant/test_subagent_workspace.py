import asyncio

from konden.assistant.builtin_tools import build_builtin_tools
from tests.fixtures.workspaces import workspace_record


def test_shared_task_reads_parent_files_and_writes_in_the_same_directory(tmp_path):
    async def scenario():
        (tmp_path / "uncommitted.txt").write_text("parent work")
        child = await build_builtin_tools(workspace_record(tmp_path))
        result = await child.execute("write_file", {"path": "child.txt", "content": "child work"})
        assert result["ok"] is True
        assert (tmp_path / "child.txt").read_text() == "child work"
        result = await child.execute("read_file", {"path": "uncommitted.txt"})
        assert "parent work" in str(result)
    asyncio.run(scenario())
