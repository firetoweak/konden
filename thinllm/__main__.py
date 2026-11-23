"""Authorize ChatGPT plan usage and export the connection configuration."""
from __future__ import annotations

import argparse
import json
import sys

from thinllm.auth import chatgpt_auth_path, sign_in_chatgpt
from thinllm.errors import InvalidLLMResponse, LLMProviderError, LLMTransientError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m thinllm", description="授权 ChatGPT 订阅并导出连接设置")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="授权模型连接")
    providers = auth.add_subparsers(dest="provider", required=True)
    chatgpt = providers.add_parser("chatgpt", help="授权使用 ChatGPT 订阅额度")
    chatgpt.add_argument("--auth-file", default="", help="授权文件路径，默认 ~/.thinllm/chatgpt.json")
    args = parser.parse_args(argv)
    path = chatgpt_auth_path(args.auth_file)
    try:
        print("即将打开浏览器授权使用 ChatGPT 订阅额度。", file=sys.stderr)
        sign_in_chatgpt(path)
    except KeyboardInterrupt:
        print("\nChatGPT 授权已取消。", file=sys.stderr)
        return 130
    except (InvalidLLMResponse, LLMProviderError, LLMTransientError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"openai-chatgpt": {"auth_file": path.as_posix()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
