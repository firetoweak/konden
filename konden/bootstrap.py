from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
import os
from pathlib import Path

from konden.assistant.conversations import AssistantQueries
from konden.assistant.host.data_root import hold_data_root
from konden.assistant.host.process_env import install_host_environment
from konden.assistant.host.session_store import SessionStore
from konden.assistant.host.supervisor import HostSupervisor
from konden.config import (
    AppConfig,
    assistant_config_from_app,
    config_path,
    load_app_config,
    write_json,
)
from konden.filesystem.registry import WorkspaceRegistry
from konden.llm.connections import ModelConnections
from konden.mcp.composition import build_mcp
from konden.model_settings import ModelSettings
from konden.paths import KondenHome
from konden.skills.composition import build_skills
from konden.skills.summarizer import LlmSkillDiffSummarizer
from konden.timing import install_timing_logging
from thinllm import create_client


class UnboundHostLlm:
    async def chat(self, *args, **kwargs):
        raise RuntimeError("Worker must bind the Host LLM port")


def worker_config(app_config: AppConfig):
    """Worker 只拿配置；它这条会话的工作区由会话自己的日志决定。"""
    return assistant_config_from_app(app_config, UnboundHostLlm())


@dataclass(frozen=True, slots=True)
class BootstrappedAssistant:
    config: AppConfig
    sessions: HostSupervisor
    sessions_root: Path
    mcp_service: object
    skill_service: object
    queries: AssistantQueries
    workspaces: WorkspaceRegistry


@asynccontextmanager
async def bootstrap_assistant(
    sink,
    *,
    app_config: AppConfig | None = None,
    workspace_path: Path | None = None,
    context_usage_sink=None,
    conversation_status_sink=None,
    tool_progress_sink=None,
    preview_sink=None,
    thinking_sink=None,
    subagent_output_sink=None,
    session_activity_sink=None,
    session_failed_sink=None,
    schedule_changed_sink=None,
    group_changed_sink=None,
) -> AsyncIterator[BootstrappedAssistant]:
    """Host 将各会话已交付的输出和可用的预览交给 Channel。"""
    install_timing_logging()
    command_environment = dict(os.environ)
    install_host_environment()
    home = KondenHome.default()
    # 先独占数据根，再碰任何 Journal、定时或持续进程。
    with hold_data_root(home):
        config = load_app_config() if app_config is None else app_config
        home.initialize()
        store = SessionStore(home.runtime_sessions_root)
        if app_config is not None and not config_path().exists():
            write_json(config_path(), app_config.to_dict())
        models = ModelSettings(home, store.root)
        if app_config is not None:
            models.save(app_config.to_dict())
        llm = ModelConnections(home.connections_path, client_factory=create_client)
        workspaces = WorkspaceRegistry.load(home.workspaces_path)
        if workspace_path is not None:
            workspaces.register_path(workspace_path)
        host = HostSupervisor(
            store,
            partial(worker_config, config),
            home,
            sink,
            workspaces=workspaces,
            llm=llm,
            command_environment=command_environment,
            models=models,
            context_usage_sink=context_usage_sink,
            conversation_status_sink=conversation_status_sink,
            tool_progress_sink=tool_progress_sink,
            preview_sink=preview_sink,
            thinking_sink=thinking_sink,
            subagent_output_sink=subagent_output_sink,
            session_activity_sink=session_activity_sink,
            session_failed_sink=session_failed_sink,
            schedule_changed_sink=schedule_changed_sink,
            group_changed_sink=group_changed_sink,
        )
        mcp = build_mcp(home)
        skills = build_skills(
            home, diff_summarizer=LlmSkillDiffSummarizer(llm, lambda: models.config().default_model or "")
        )
        async with llm, mcp.client_manager, asyncio.TaskGroup() as tasks:
            try:
                # Take over old attempts before clocks can deliver new work.
                await host.resume_unfinished_sessions()
                host.start_automation(tasks)
                yield BootstrappedAssistant(
                    config,
                    host,
                    store.root,
                    mcp.service,
                    skills.service,
                    AssistantQueries(store, host),
                    workspaces,
                )
            finally:
                await host.close()
