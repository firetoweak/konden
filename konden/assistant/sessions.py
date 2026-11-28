from __future__ import annotations

import asyncio
from dataclasses import dataclass

from konden.assistant.catalog import CapabilityCatalog
from konden.assistant.clarification import ANSWER_SOURCE, questions_by_id
from konden.assistant.control import (
    CONTROL_SOURCE,
    AssistantControlPlane,
    ControlApprovalView,
    ControlDecisionConflict,
    ControlOutcome,
    NoPendingControlApproval,
    pending_approval_view,
    project_control,
    project_control_message,
)
from konden.assistant.inputs import InputConflict
from konden.assistant.management import ManagementSurface
from konden.assistant.runner import (
    SessionScheduler,
    resume_session,
)
from konden.assistant.subagent.subagent import SubAgentHost, project_pending
from konden.assistant.toolsets import ToolSurface
from konden.runtime import AgentRuntime, DeliveryConflictError, RuntimeStatus
from konden.runtime.model import CanonicalState, CommandPhase


@dataclass(frozen=True, slots=True)
class SessionView:
    status: str
    waiting_for: tuple[str, ...]
    should_wake: bool
    has_open_subagents: bool = False
    control_approval: ControlApprovalView | None = None
    control_message: str | None = None
    last_error: str | None = None


def session_view(
    state: CanonicalState,
    *,
    control_approval: ControlApprovalView | None = None,
    control_message: str | None = None,
    has_open_subagents: bool = False,
    last_error: str | None = None,
) -> SessionView:
    return SessionView(
        status=state.status.value,
        waiting_for=state.waiting_for,
        should_wake=(
            state.status is RuntimeStatus.RUNNABLE
            or any(
                command.phase is CommandPhase.PENDING for command in state.commands
            )
        ),
        has_open_subagents=has_open_subagents,
        control_approval=control_approval,
        control_message=control_message,
        last_error=last_error,
    )


class AssistantSessions:
    """给定 Session identity 后的 Assistant 应用操作。"""

    def __init__(
        self,
        runtime: AgentRuntime,
        surface: ToolSurface,
        scheduler: SessionScheduler,
        *,
        control: AssistantControlPlane,
        management: ManagementSurface,
        catalog: CapabilityCatalog,
        subagents: SubAgentHost | None = None,
    ) -> None:
        self._runtime = runtime
        self._surface = surface
        self._scheduler = scheduler
        self._control = control
        self._management = management
        self._catalog = catalog
        self._subagents = subagents
        self._control_locks: dict[str, asyncio.Lock] = {}

    def _view(
        self,
        state: CanonicalState,
        events,
        *,
        has_open_subagents: bool = False,
    ) -> SessionView:
        return session_view(
            state,
            control_approval=pending_approval_view(events),
            control_message=project_control_message(events),
            has_open_subagents=has_open_subagents,
        )

    async def create(self, session_id: str) -> SessionView:
        created = await self._runtime.create_session(session_id)
        if not created:
            raise ValueError(f"Session 已存在: {session_id}")
        return await self.view(session_id)

    async def resume(self, session_id: str) -> SessionView:
        await resume_session(
            self._runtime,
            self._surface,
            session_id,
            self._management,
            self._catalog,
        )
        await self.recover_control(session_id)
        pending_subagents: tuple[str, ...] = ()
        if self._subagents is not None:
            pending_subagents = await self._subagents.rehydrate(session_id)
        events = await self._runtime.snapshot(session_id)
        state = await self._runtime.state(session_id)
        if self._subagents is not None and self._subagents.has_returned(session_id):
            return self._view(state, events)
        if self._view(state, events).should_wake:
            await self._scheduler.wake(session_id)
        return self._view(
            state,
            events,
            has_open_subagents=bool(pending_subagents),
        )

    async def view(self, session_id: str) -> SessionView:
        events = await self._runtime.snapshot(session_id)
        state = await self._runtime.state(session_id)
        has_open_subagents = False
        if self._subagents is not None:
            has_open_subagents = bool(project_pending(events))
        return self._view(
            state,
            events,
            has_open_subagents=has_open_subagents,
        )

    async def resolve_control(
        self,
        session_id: str,
        request_id: str,
        *,
        approved: bool,
    ) -> str:
        async with self._control_locks.setdefault(session_id, asyncio.Lock()):
            events = await self._runtime.snapshot(session_id)
            projection = project_control(events)
            state = projection.get(request_id)
            if state is None:
                raise ControlDecisionConflict(f"未知控制请求: {request_id}")
            if state.phase == "rejected":
                if approved:
                    raise ControlDecisionConflict("控制请求已经被拒绝")
                assert state.message is not None
                return state.message
            if state.phase in {"succeeded", "failed"}:
                if not approved:
                    raise ControlDecisionConflict("控制请求已经被批准并执行")
                assert state.message is not None
                return state.message
            if state.phase == "execution_started":
                if not approved:
                    raise ControlDecisionConflict("控制请求已经开始执行")
                assert projection.message is not None
                return projection.message
            if state.phase == "approved":
                if not approved:
                    raise ControlDecisionConflict("控制请求已经被批准")
                return await self._execute_approved(session_id)
            if state.phase != "proposed" or projection.active is not state:
                raise NoPendingControlApproval(session_id)
            assert state.request is not None
            decision = self._control.decision_outcome(
                state.request,
                approved=approved,
            )
            await self._commit_control(session_id, decision)
            if not approved:
                await self._scheduler.wake(session_id)
                return decision.data["message"]
            return await self._execute_approved(session_id)

    async def _commit_control(
        self,
        session_id: str,
        outcome: ControlOutcome,
    ) -> None:
        await self._runtime.receive_domain_fact(
            session_id,
            outcome.fact_type,
            dict(outcome.data),
            delivery_id=outcome.delivery_id,
            source=CONTROL_SOURCE,
            requests_decision=outcome.requests_decision,
        )

    async def _execute_approved(self, session_id: str) -> str:
        projection = project_control(await self._runtime.snapshot(session_id))
        state = projection.active
        if state is None or state.phase != "approved" or state.request is None:
            raise RuntimeError("控制执行没有已批准请求")
        request = state.request
        await self._commit_control(
            session_id,
            self._control.execution_started_outcome(request),
        )
        execution = await self._control.execute(request)
        terminal = self._control.terminal_outcome(request, execution)
        await self._commit_control(session_id, terminal)
        await self._scheduler.wake(session_id)
        return execution.message

    async def recover_control(self, session_id: str) -> None:
        async with self._control_locks.setdefault(session_id, asyncio.Lock()):
            outcome = await self._control.prepare_pending(
                await self._runtime.snapshot(session_id)
            )
            if outcome is not None:
                await self._commit_control(session_id, outcome)
                if outcome.requests_decision:
                    await self._scheduler.wake(session_id)
            active = project_control(
                await self._runtime.snapshot(session_id)
            ).active
            if active is not None and active.phase == "approved":
                await self._execute_approved(session_id)

    async def receive_user_message(
        self,
        session_id: str,
        content: str,
        *,
        delivery_id: str,
        source: str = "user",
        artifact_refs: tuple[str, ...] = (),
    ) -> None:
        try:
            await self._runtime.receive_user_message(
                session_id,
                content,
                delivery_id=delivery_id,
                source=source,
                artifact_refs=tuple(artifact_refs),
            )
        except DeliveryConflictError as error:
            if source == ANSWER_SOURCE:
                raise InputConflict("这个问题已有其他回答。") from error
            raise InputConflict("该消息编号已用于不同内容，请重新发送消息。") from error
        await self._scheduler.wake(session_id)

    async def accept_input(
        self,
        session_id: str,
        content: str,
        *,
        delivery_id: str,
        source: str = "user",
        artifact_refs: tuple[str, ...] = (),
    ) -> SessionView:
        if source == ANSWER_SOURCE:
            if delivery_id not in questions_by_id(await self._runtime.snapshot(session_id)):
                raise InputConflict("当前会话没有这个澄清问题。")
            artifact_refs = ()
        await self.receive_user_message(
            session_id,
            content,
            delivery_id=delivery_id,
            source=source,
            artifact_refs=tuple(artifact_refs),
        )
        return await self.view(session_id)

    async def cancel_turn(self, session_id: str) -> SessionView:
        await self._scheduler.cancel_turn(session_id)
        return await self.view(session_id)
