"""验证地图引用的 HTTP 端点和会话状态字段必须存在。"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
import re
import unittest

from tests.architecture.web_routes import unresolved_routes

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "web" / "src" / "api" / "contracts.ts"
SESSIONS = ROOT / "konden" / "assistant" / "sessions.py"
FEATURES = ROOT / ".claude" / "skills" / "verify-konden" / "features"

MAP_SECTIONS = ("How to get to it", "Driving it with")
DOC_ROUTE = re.compile(
    r"(?:^|\s)([A-Z]+(?:/[A-Z]+)*)\s+((?:/api\b|\.\.\./)(?:<[^>/]+>|\{[^}/]+\}|[^\s<>{}])*)"
)


def map_route_references(text: str, filename: str) -> Iterator[tuple[str, str, str]]:
    active = False
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            active = line[3:].startswith(MAP_SECTIONS)
        if not active:
            continue
        for span in re.finditer(r"`([^`]+)`", line):
            code = span[1]
            # 地图既写 `GET /api/...`，也写 GET/PUT `/api/...`。
            if before := re.search(r"\b([A-Z]+(?:/[A-Z]+)*)\s+$", line[:span.start()]):
                code = before[1] + " " + code
            if match := DOC_ROUTE.search(code):
                path = match[2].partition("?")[0]
                if path.startswith(".../"):
                    path = "/api/sessions/{id}/" + path[4:]
                for verb in match[1].split("/"):
                    yield f"{filename}:{line_number}", verb, path


def zod_object_keys(source: str, name: str) -> set[str]:
    """取出 `name` 所定义对象的第一层键名；声明可能跨行写作 `z\\n  .object({`。"""
    brace = source.index("{", source.index(".object(", source.index(name)))
    depth = 0
    body = ""
    for offset in range(brace, len(source)):
        char = source[offset]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                body = source[brace + 1 : offset]
                break
    keys: set[str] = set()
    depth = 0
    for line in body.splitlines():
        if depth == 0:
            match = re.match(r"\s*([a-z_][a-z0-9_]*)\s*:", line)
            if match:
                keys.add(match.group(1))
        depth += line.count("{") - line.count("}")
    return keys


def dataclass_fields(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            }
    return set()


def map_session_keys() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(FEATURES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        found.extend((path.name, key) for key in re.findall(r"`session\.([a-z_][a-z0-9_]*)`", text))
    return found


class ReferenceConsistencyTest(unittest.TestCase):
    def test_feature_map_routes_exist_on_the_backend(self):
        references = [
            reference for path in sorted(FEATURES.glob("*.md"))
            for reference in map_route_references(path.read_text(encoding="utf-8"), path.name)
        ]
        self.assertTrue(references)
        self.assertEqual(unresolved_routes(references), [])

    def test_map_notation_preserves_each_documented_method_and_path(self):
        text = """## Driving it with the browser
`POST .../control` and `GET /api/sessions/<父 id>/subagents/<delegate command id>`
GET/PUT `/api/shared-memory`
`curl -X POST .../inputs`
## Gotchas
`GET /api/retired`
"""
        self.assertEqual(list(map_route_references(text, "map.md")), [
            ("map.md:2", "POST", "/api/sessions/{id}/control"),
            ("map.md:2", "GET", "/api/sessions/<父 id>/subagents/<delegate command id>"),
            ("map.md:3", "GET", "/api/shared-memory"),
            ("map.md:3", "PUT", "/api/shared-memory"),
            ("map.md:4", "POST", "/api/sessions/{id}/inputs"),
        ])

    def test_feature_map_session_keys_exist_in_both_schemas(self):
        """地图里的 session.* 键必须同时出现在后端视图和前端 schema 里。"""
        backend = dataclass_fields(SESSIONS, "SessionView")
        self.assertIn("auto_authorize", backend)
        frontend = zod_object_keys(CONTRACTS.read_text(encoding="utf-8"), "sessionViewSchema")
        self.assertIn("auto_authorize", frontend)
        offenders = [
            f"{filename}: session.{key} 不在 {'后端视图' if key not in backend else '前端 schema'}"
            for filename, key in map_session_keys()
            if key not in backend or key not in frontend
        ]
        self.assertEqual(offenders, [])
