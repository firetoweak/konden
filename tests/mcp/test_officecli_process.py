"""Real upstream MCP must edit and preview a document in the Bot's directory."""
import asyncio
from base64 import b64decode
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from PIL import Image
import pytest

from konden.mcp.bundled import OFFICECLI_SERVER_ID
from konden.mcp.composition import build_mcp
from konden.paths import KondenHome


pytestmark = pytest.mark.process


def test_officecli_edits_previews_and_restores_without_installation(tmp_path):
    home = KondenHome(tmp_path / "home")
    home.initialize()
    root = tmp_path / "中文 工作目录"
    root.mkdir()
    filename = "中文 报告.docx"

    async def scenario():
        first = build_mcp(home, task_root=root)
        toolset_id = "mcp:" + OFFICECLI_SERVER_ID
        assert toolset_id not in {item.id for item in first.toolset_provider.descriptors(read_only_only=True)}
        async with asyncio.timeout(120):
            async with first.client_manager:
                (loaded,) = await first.toolset_provider.discover_tools(toolset_id)
                for command in (
                    ["create", filename, "--locale", "zh-CN"],
                    ["add", filename, "/body", "--type", "paragraph", "--prop", "text=初稿"],
                    ["set", filename, "/body/p[1]", "--prop", "text=已核对的中文内容"],
                    ["validate", filename, "--json"],
                ):
                    result = await loaded.spec.handler({"command": command})
                    assert result["ok"] is True, result
                with ZipFile(root / filename) as document:
                    tree = ElementTree.fromstring(document.read("word/document.xml"))
                assert "已核对的中文内容" in "".join(tree.itertext())
                preview = await loaded.spec.handler({"command": [
                    "view", filename, "screenshot", "--render", "html", "-o", "预览.png",
                ]})
                assert preview["ok"] is True, preview
                (image,) = [block for block in preview["data"]["mcp"]["content"] if block["type"] == "image"]
                image_bytes = b64decode(image["data"])
                with Image.open(BytesIO(image_bytes)) as bitmap:
                    assert bitmap.width > 0 and bitmap.height > 0
                assert (root / "预览.png").read_bytes() == image_bytes

            second = build_mcp(home, task_root=root)
            async with second.client_manager:
                descriptor = next(item for item in second.toolset_provider.descriptors() if item.id == toolset_id)
                restored = second.toolset_provider.restore_spec(
                    toolset_id=toolset_id, revision=descriptor.revision,
                    name=loaded.spec.name, description=loaded.spec.description,
                    parameters=loaded.spec.parameters.schema(),
                    requires_authorization=loaded.spec.requires_authorization,
                    provider_data=loaded.provider_data,
                )
                missing = await restored.handler({"command": ["get", "不存在.docx", "/body"]})
                assert missing["ok"] is False
                result = await restored.handler({"command": ["view", filename, "text"]})
                assert result["ok"] is True, result
                assert "已核对的中文内容" in str(result)
        assert first.service.registry.snapshot() == second.service.registry.snapshot() == ()
        assert not (home.mcp_root / "secrets").exists()

    asyncio.run(scenario())
