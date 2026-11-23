import argparse
from copy import deepcopy
import logging
import os
from pathlib import Path
import subprocess
from urllib.parse import unquote

import uvicorn

from konden.assistant.host.process_env import host_process_environment
from konden.channels.web import create_web_app
from konden.channels.web.chatgpt_authorization import CALLBACK_PATH

STATIC_DIR = Path(__file__).resolve().parent / "web" / "dist"


class _RequestLogFilter(logging.Filter):
    def __init__(self, development: bool):
        super().__init__()
        self.development = development

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn records include the query string, which carries the OAuth code.
        path = unquote(record.args[2].partition("?")[0])
        return path != CALLBACK_PATH and (self.development or record.args[4] >= 400)


def production_app(workspace_path: Path | None = None):
    return create_web_app(workspace_path=workspace_path,
                          static_dir=STATIC_DIR if STATIC_DIR.is_dir() else None)


app = production_app()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dev",
        action="store_true",
        help="同时启动 Vite 开发服务器",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Web 监听地址；局域网共享时使用 0.0.0.0",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Web 服务端口",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="显式登记工作区路径；缺省不从启动目录隐式创建",
    )
    args = parser.parse_args(argv)
    # Uvicorn's Windows reloader requires SelectorEventLoop, which cannot run
    # our async CLI calls. Proactor cannot reuse its listener after a reload.
    reload_backend = args.dev and os.name != "nt"

    log_config = deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["filters"] = {"requests": {"()": _RequestLogFilter, "development": args.dev}}
    log_config["handlers"]["access"]["filters"] = ["requests"]

    frontend = None
    if args.dev:
        if os.name == "nt":
            logging.getLogger("uvicorn.error").warning(
                "Windows 开发模式：前端自动更新；修改 Python 后请重启此命令。"
            )
        web = Path(__file__).parent / "web"
        frontend = subprocess.Popen(
            ["node", web / "node_modules" / "vite" / "bin" / "vite.js"],
            cwd=web,
            env=host_process_environment(),
            # Uvicorn uses Ctrl+C for reloads on Windows; Vite must survive them.
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

    try:
        uvicorn.run(
            "web_chat:app" if reload_backend else production_app(args.workspace),
            host=args.host,
            port=args.port,
            reload=reload_backend,
            reload_dirs=[str(Path(__file__).parent)] if reload_backend else None,
            log_config=log_config,
            timeout_graceful_shutdown=1,
        )
    finally:
        if frontend is not None and frontend.poll() is None:
            frontend.terminate()
            frontend.wait()


if __name__ == "__main__":
    main()
