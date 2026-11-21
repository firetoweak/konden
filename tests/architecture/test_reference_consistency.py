"""引用一致性：写下来的路由、状态键和链接必须解析到真实存在的东西。

同一份事实散在代码、验证地图和文档里，靠人记住同步。这里把"写下来的引用"当成数据，
逐个确认它指向的东西仍然存在；引用了不存在的东西即失败。
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "konden" / "channels" / "web" / "app.py"
API_CLIENT = ROOT / "web" / "src" / "api" / "kondenApi.ts"
CONTRACTS = ROOT / "web" / "src" / "api" / "contracts.ts"
SESSIONS = ROOT / "konden" / "assistant" / "sessions.py"
FEATURES = ROOT / ".claude" / "skills" / "verify-konden" / "features"
DOC_ROOTS = (ROOT / "docs", ROOT / ".claude" / "skills")
DOC_FILES = (ROOT / "AGENTS.md", ROOT / "README.md", ROOT / "review.md")

VERBS = {"get", "post", "put", "delete", "patch"}
PLACEHOLDER = re.compile(r"\{[^}]*\}|<[^<>\s/]*>")
DOC_ROUTE = re.compile(r"`([A-Z]+(?:/[A-Z]+)*)\s+(/api[^`\s]*)`")
SECTION = re.compile(r"^## (.+)$", re.MULTILINE)
MAP_SECTIONS = ("How to get to it", "Driving it with")


def route_exists(path: str, routes: set[str]) -> bool:
    """按段比对：地图常把占位符写成示例值（`nope`、`session-不存在`），段数和字面段对上即算存在。"""
    segments = path.strip("/").split("/")
    for route in routes:
        candidate = route.strip("/").split("/")
        if len(candidate) != len(segments):
            continue
        if all(left == "{}" or right == "{}" or left == right
               for left, right in zip(segments, candidate, strict=True)):
            return True
    return False


def normalize_route(path: str) -> str:
    path = re.sub(r"\$\{[^}]*\}", "{}", path.split("?", 1)[0])
    path = PLACEHOLDER.sub("{}", path)
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    return path


def backend_routes() -> set[str]:
    """从 FastAPI 装饰器取出后端真实存在的路径。"""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            owner, verb = decorator.func.value, decorator.func.attr
            if not (isinstance(owner, ast.Name) and owner.id == "app" and verb in VERBS):
                continue
            if decorator.args:
                first = decorator.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    routes.add(normalize_route(first.value))
    return routes


def frontend_routes() -> set[str]:
    """从前端请求路径字面量取出它期待的 API 路径；baseUrl 是 /api。"""
    source = API_CLIENT.read_text(encoding="utf-8")
    literals = re.findall(r'"(\/[^"\n]*)"', source) + re.findall(r"`(\/[^`]*)`", source)
    paths: set[str] = set()
    for literal in literals:
        if not literal.startswith("/") or literal == "/api":
            continue
        paths.add(normalize_route(literal if literal.startswith("/api") else "/api" + literal))
    return paths


def map_route_references() -> list[tuple[str, str, str]]:
    """取地图两节里写成 METHOD /api/... 的引用，返回 (文件, 方法, 路径)。"""
    references: list[tuple[str, str, str]] = []
    for path in sorted(FEATURES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        matches = list(SECTION.finditer(text))
        for index, match in enumerate(matches):
            if not match.group(1).startswith(MAP_SECTIONS):
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.start():end]
            for verbs, target in DOC_ROUTE.findall(body):
                if "*" in target:
                    continue
                target = target.rstrip("，。；、)）")
                if target.startswith("..."):
                    target = "/api/sessions/{}" + target[3:]
                references.extend((path.name, verb, normalize_route(target))
                                  for verb in verbs.split("/"))
    return references


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


def relative_links(path: Path) -> list[tuple[int, str]]:
    """剥掉围栏代码块与行内代码后取相对链接；代码里的方括号不是链接。"""
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "", text)
    links: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", line):
            target = target.split("#", 1)[0].strip()
            if target and not target.startswith(("http://", "https://", "mailto:")):
                links.append((line_number, target))
    return links


class ReferenceConsistencyTest(unittest.TestCase):
    def test_frontend_endpoints_exist_on_the_backend(self):
        """前端写下的每个请求路径都必须对应一条真实路由。"""
        routes = backend_routes()
        self.assertIn("/api/agents", routes)
        offenders = sorted(path for path in frontend_routes() if path not in routes)
        self.assertEqual(offenders, [])

    def test_feature_map_routes_exist_on_the_backend(self):
        """验证地图引用的路由必须真实存在；占位符写成示例值时按段比对。"""
        routes = backend_routes()
        offenders = [
            f"{filename}: {verb} {path}"
            for filename, verb, path in map_route_references()
            if not route_exists(path, routes)
        ]
        self.assertEqual(offenders, [])

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

    def test_markdown_relative_links_resolve(self):
        """文档里的相对链接必须指向存在的文件。"""
        files = [*sorted(DOC_ROOTS[0].rglob("*.md")), *sorted(DOC_ROOTS[1].rglob("*.md"))]
        files.extend(path for path in DOC_FILES if path.is_file())
        offenders = [
            f"{path.relative_to(ROOT).as_posix()}:{number} -> {target}"
            for path in files
            for number, target in relative_links(path)
            if not (path.parent / target).exists()
        ]
        self.assertEqual(offenders, [])
