"""按 FastAPI 装饰器核对地图和前端请求。Web 检查使用已安装的前端依赖。"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "konden" / "channels" / "web" / "app.py"
VERBS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def route_pattern(path: str) -> re.Pattern[str]:
    segments = []
    for segment in path.rstrip("/").split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            converter = segment[1:-1].partition(":")[2] or "str"
            segments.append({"str": "[^/]+", "path": ".*"}[converter])
        else:
            segments.append(re.escape(segment))
    return re.compile("/".join(segments))


def backend_routes() -> dict[str, list[re.Pattern[str]]]:
    """从 FastAPI 装饰器读取方法；只有后端声明的参数段能匹配示例值。"""
    routes = defaultdict(list)
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            match decorator:
                case ast.Call(func=ast.Attribute(value=ast.Name(id="app"), attr=verb)) if verb in VERBS:
                    path = decorator.args[0] if decorator.args else next(
                        kw.value for kw in decorator.keywords if kw.arg == "path"
                    )
                    routes[verb.upper()].append(route_pattern(ast.literal_eval(path)))
    return routes


def unresolved_routes(references: Iterable[tuple[str, str, str]]) -> list[str]:
    routes = backend_routes()
    return [
        f"{source}: {method} {path}"
        for source, method, path in references
        if not any(pattern.fullmatch(path.rstrip("/")) for pattern in routes.get(method, ()))
    ]


if __name__ == "__main__":
    requests = json.loads(subprocess.check_output(
        ["node", ROOT / "web" / "tests" / "endpointReferences.mjs"], encoding="utf-8"
    ))
    if errors := unresolved_routes(requests):
        raise SystemExit("\n".join(errors))
    print(f"{len(requests)} client HTTP methods and paths verified")
