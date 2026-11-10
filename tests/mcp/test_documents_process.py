"""A textless PDF page must reach model images; a crop must show the requested region."""
import asyncio
from base64 import b64decode
from io import BytesIO
import zlib

from PIL import Image
import pytest

from konden.assistant.attachments import AttachmentStore
from konden.assistant.mcp import extract_images
from konden.mcp.bundled import DOCUMENT_SERVER_ID
from konden.mcp.composition import build_mcp
from konden.paths import KondenHome


pytestmark = pytest.mark.process


def mixed_pdf():
    """Independent fixture: two text columns and vector colors, then a raster-only page."""
    def stream(data, attributes=b""):
        return b"<< /Length %d %s >>\nstream\n" % (len(data), attributes) + data + b"\nendstream"

    content = (
        b"BT /F1 18 Tf 30 550 Td (TWO COLUMN CHECK) Tj ET\n"
        b"BT /F1 12 Tf 30 490 Td (LEFT ONE: 37) Tj 0 -30 Td (LEFT TWO: 41) Tj ET\n"
        b"BT /F1 12 Tf 220 490 Td (RIGHT ONE: 83) Tj 0 -30 Td (RIGHT TWO: 97) Tj ET\n"
        b"1 0 0 rg 30 100 160 130 re f\n0 0 1 rg 220 100 150 130 re f\n"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        stream(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] /Resources << /XObject << /Im1 8 0 R >> >> /Contents 7 0 R >>",
        stream(b"q 400 0 0 600 0 0 cm /Im1 Do Q"),
        stream(zlib.compress(bytes([255, 165, 0]) * (200 * 300)),
               b"/Type /XObject /Subtype /Image /Width 200 /Height 300 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"),
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + value + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return data + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_documents_render_scanned_pages_crop_and_restore(tmp_path):
    home = KondenHome(tmp_path / "home")
    home.initialize()
    root = tmp_path / "中文 工作目录"
    root.mkdir()
    original = mixed_pdf()
    document = root / "双栏 与扫描页.pdf"
    document.write_bytes(original)
    images = AttachmentStore(tmp_path / "attachments")
    toolset_id = "mcp:" + DOCUMENT_SERVER_ID

    async def scenario():
        assembly = build_mcp(home, task_root=root)
        assert toolset_id not in {item.id for item in assembly.toolset_provider.descriptors(read_only_only=True)}
        async with asyncio.timeout(60):
            async with assembly.client_manager:
                loaded = await assembly.toolset_provider.discover_tools(toolset_id)
                tools = {item.provider_data["tool_name"]: item for item in loaded}
                read = await tools["read"].spec.handler({"source": str(document), "images": "none", "ocr": False})
                assert read["ok"] is True, read
                assert "LEFT ONE: 37" in str(read) and "RIGHT ONE: 83" in str(read)
                assert "scanned_pages: [2]" in str(read)
                rendered = await tools["inspect"].spec.handler({
                    "operation": "render_page", "sources": [{"path": str(document), "pages": "2"}], "scale": 2,
                })
                assert rendered["ok"] is True, rendered
                (block,) = [item for item in rendered["data"]["mcp"]["content"] if item["type"] == "image"]
                with Image.open(BytesIO(b64decode(block["data"]))) as bitmap:
                    assert bitmap.size == (800, 1200)
                    assert bitmap.getpixel((400, 600))[:3] == (255, 165, 0)
                projected = extract_images(rendered, images)
                (image,) = projected["images"]
                assert image["type"] == "image" and image["id"].startswith("sha256:")
                inspect = tools["inspect"]
                descriptor = next(item for item in assembly.toolset_provider.descriptors() if item.id == toolset_id)

            restored = build_mcp(home, task_root=root)
            spec = restored.toolset_provider.restore_spec(
                toolset_id=toolset_id, revision=descriptor.revision,
                name=inspect.spec.name, description=inspect.spec.description,
                parameters=inspect.spec.parameters.schema(), requires_authorization=inspect.spec.requires_authorization,
                provider_data=inspect.provider_data,
            )
            async with restored.client_manager:
                result = await spec.handler({
                    "operation": "extract_regions", "scale": 3,
                    "sources": [{"path": str(document), "regions": [{"page": 1, "bounding_box": {
                        "left": 50, "bottom": 120, "right": 170, "top": 210,
                    }}]}],
                })
                assert result["ok"] is True, result
                (block,) = [item for item in result["data"]["mcp"]["content"] if item["type"] == "image"]
                with Image.open(BytesIO(b64decode(block["data"]))) as bitmap:
                    assert bitmap.size == (360, 270)
                    assert bitmap.getpixel((180, 135))[:3] == (255, 0, 0)
        assert document.read_bytes() == original
        assert assembly.service.registry.snapshot() == restored.service.registry.snapshot() == ()
        assert not (home.root / "cache/anymd/models").exists()

    asyncio.run(scenario())
