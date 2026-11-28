import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

export type SessionActivity = "running" | "idle";

export type ActivePreview = {
  outputId: string;
  text: string;
};

export type LiveTool = {
  commandId: string;
  name: string;
  status: "running";
};

export type ToolProgressStatus = "running" | "settled";

export type OutgoingUserMessage = {
  deliveryId: string;
  text: string;
  artifactRefs: string[];
  images: string[];
  files: { attachment_id: string; name: string; size: number }[];
  createdAt: string;
  status: "sending" | "failed";
  error: string | null;
};

export type OutgoingGroupMessage = {
  deliveryId: string;
  text: string;
  mention: string[];
  createdAt: string;
  status: "sending" | "failed";
  error: string | null;
};

export type SessionRuntime = {
  activity: SessionActivity | null;
  lastError: string | null;
  activePreview: ActivePreview | null;
  activeThinking: ActivePreview | null;
  unread: number;
  committed: Record<string, string>;
  committedThinking: Record<string, string>;
  tools: Record<string, LiveTool>;
  controlNotice: string | null;
  conversationStatus: {
    compactCount: number;
    compactPhase: "running" | "ready" | "failed" | null;
  } | null;
  outgoing: OutgoingUserMessage[];
};

export type RuntimeState = {
  connectionId: string | null;
  viewingSessionId: string | null;
  ownerSessionId: string | null;
  sessions: Record<string, SessionRuntime>;
  groupOutgoing: Record<string, OutgoingGroupMessage[]>;
};

const initialState: RuntimeState = {
  connectionId: null,
  viewingSessionId: null,
  ownerSessionId: null,
  sessions: {},
  groupOutgoing: {},
};

function runtimeOf(state: RuntimeState, sessionId: string): SessionRuntime {
  const current = state.sessions[sessionId];
  if (current !== undefined) {
    return current;
  }
  const created: SessionRuntime = {
    activity: null,
    lastError: null,
    activePreview: null,
    activeThinking: null,
    unread: 0,
    committed: {},
    committedThinking: {},
    tools: {},
    controlNotice: null,
    conversationStatus: null,
    outgoing: [],
  };
  state.sessions[sessionId] = created;
  return created;
}

const runtimeSlice = createSlice({
  name: "runtime",
  initialState,
  reducers: {
    connected(state, action: PayloadAction<string>) {
      state.connectionId = action.payload;
    },
    disconnected(state) {
      state.connectionId = null;
      state.ownerSessionId = null;
      for (const session of Object.values(state.sessions)) {
        session.activity = null;
        session.activePreview = null;
        session.activeThinking = null;
        session.committedThinking = {};
        session.tools = {};
      }
    },
    viewing(state, action: PayloadAction<string | null>) {
      state.viewingSessionId = action.payload;
      if (action.payload !== null) {
        runtimeOf(state, action.payload).unread = 0;
      }
    },
    bindOwner(state, action: PayloadAction<string>) {
      state.ownerSessionId = action.payload;
    },
    clearLiveOutput(state, action: PayloadAction<string>) {
      const session = runtimeOf(state, action.payload);
      session.lastError = null;
      session.activePreview = null;
      session.activeThinking = null;
      session.committed = {};
      session.committedThinking = {};
      session.tools = {};
      session.controlNotice = null;
      session.conversationStatus = null;
    },
    controlNotice(
      state,
      action: PayloadAction<{ sessionId: string; message: string | null }>,
    ) {
      runtimeOf(state, action.payload.sessionId).controlNotice =
        action.payload.message;
    },
    sessionActivity(
      state,
      action: PayloadAction<{ sessionId: string; activity: SessionActivity }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      session.activity = action.payload.activity;
      if (action.payload.activity === "running") {
        session.lastError = null;
      } else {
        session.tools = {};
      }
    },
    sessionFailed(
      state,
      action: PayloadAction<{ sessionId: string; message: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      session.lastError = action.payload.message;
      session.activity = "idle";
      session.tools = {};
    },
    previewStarted(
      state,
      action: PayloadAction<{ sessionId: string; outputId: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      session.activePreview = { outputId: action.payload.outputId, text: "" };
    },
    previewDelta(
      state,
      action: PayloadAction<{ sessionId: string; outputId: string; text: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      if (
        session.activePreview === null ||
        session.activePreview.outputId !== action.payload.outputId
      ) {
        return;
      }
      session.activePreview.text += action.payload.text;
    },
    previewAborted(
      state,
      action: PayloadAction<{ sessionId: string; outputId: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      if (session.activePreview?.outputId === action.payload.outputId) {
        session.activePreview = null;
      }
    },
    thinkingStarted(
      state,
      action: PayloadAction<{ sessionId: string; outputId: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      session.activeThinking = { outputId: action.payload.outputId, text: "" };
    },
    thinkingDelta(
      state,
      action: PayloadAction<{ sessionId: string; outputId: string; text: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      if (
        session.activeThinking === null ||
        session.activeThinking.outputId !== action.payload.outputId
      ) {
        return;
      }
      session.activeThinking.text += action.payload.text;
    },
    thinkingClosed(
      state,
      action: PayloadAction<{ sessionId: string; outputId: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      if (session.activeThinking?.outputId !== action.payload.outputId) {
        return;
      }
      if (session.activeThinking.text.trim() !== "") {
        session.committedThinking[action.payload.outputId] =
          session.activeThinking.text;
      }
      session.activeThinking = null;
    },
    outputFinal(
      state,
      action: PayloadAction<{ sessionId: string; outputId: string; text: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      const firstDelivery = !(action.payload.outputId in session.committed);
      session.committed[action.payload.outputId] = action.payload.text;
      if (session.activePreview?.outputId === action.payload.outputId) {
        session.activePreview = null;
      }
      if (firstDelivery && state.viewingSessionId !== action.payload.sessionId) {
        session.unread += 1;
      }
    },
    toolProgress(
      state,
      action: PayloadAction<{
        sessionId: string;
        commandId: string;
        name: string;
        status: ToolProgressStatus;
      }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      if (action.payload.status === "settled") {
        delete session.tools[action.payload.commandId];
        return;
      }
      session.tools[action.payload.commandId] = {
        commandId: action.payload.commandId,
        name: action.payload.name,
        status: "running",
      };
    },
    conversationStatus(
      state,
      action: PayloadAction<{
        sessionId: string;
        compactCount: number;
        compactPhase: "running" | "ready" | "failed" | null;
      }>,
    ) {
      runtimeOf(state, action.payload.sessionId).conversationStatus = {
        compactCount: action.payload.compactCount,
        compactPhase: action.payload.compactPhase,
      };
    },
    outgoingQueued(
      state,
      action: PayloadAction<{ sessionId: string; message: OutgoingUserMessage }>,
    ) {
      const outgoing = runtimeOf(state, action.payload.sessionId).outgoing;
      const existing = outgoing.findIndex((item) => item.deliveryId === action.payload.message.deliveryId);
      if (existing >= 0) {
        outgoing[existing] = action.payload.message;
      } else {
        outgoing.push(action.payload.message);
      }
    },
    outgoingConfirmed(
      state,
      action: PayloadAction<{ sessionId: string; deliveryId: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      session.outgoing = session.outgoing.filter((item) => item.deliveryId !== action.payload.deliveryId);
    },
    outgoingFailed(
      state,
      action: PayloadAction<{ sessionId: string; deliveryId: string; error: string }>,
    ) {
      const item = runtimeOf(state, action.payload.sessionId).outgoing.find(
        (entry) => entry.deliveryId === action.payload.deliveryId,
      );
      if (item !== undefined) {
        item.status = "failed";
        item.error = action.payload.error;
      }
    },
    outgoingRemoved(
      state,
      action: PayloadAction<{ sessionId: string; deliveryId: string }>,
    ) {
      const session = runtimeOf(state, action.payload.sessionId);
      session.outgoing = session.outgoing.filter((item) => item.deliveryId !== action.payload.deliveryId);
    },
    outgoingHydrated(
      state,
      action: PayloadAction<{ sessionId: string; messages: OutgoingUserMessage[] }>,
    ) {
      const outgoing = runtimeOf(state, action.payload.sessionId).outgoing;
      const present = new Set(outgoing.map((item) => item.deliveryId));
      for (const message of action.payload.messages) {
        if (!present.has(message.deliveryId)) {
          outgoing.push(message);
        }
      }
    },
    groupOutgoingQueued(
      state,
      action: PayloadAction<{ groupId: string; message: OutgoingGroupMessage }>,
    ) {
      const list = (state.groupOutgoing[action.payload.groupId] ??= []);
      const existing = list.findIndex((item) => item.deliveryId === action.payload.message.deliveryId);
      if (existing >= 0) {
        list[existing] = action.payload.message;
      } else {
        list.push(action.payload.message);
      }
    },
    groupOutgoingConfirmed(
      state,
      action: PayloadAction<{ groupId: string; deliveryId: string }>,
    ) {
      const list = state.groupOutgoing[action.payload.groupId];
      if (list === undefined) return;
      state.groupOutgoing[action.payload.groupId] = list.filter((item) => item.deliveryId !== action.payload.deliveryId);
    },
    groupOutgoingFailed(
      state,
      action: PayloadAction<{ groupId: string; deliveryId: string; error: string }>,
    ) {
      const item = state.groupOutgoing[action.payload.groupId]?.find(
        (entry) => entry.deliveryId === action.payload.deliveryId,
      );
      if (item !== undefined) {
        item.status = "failed";
        item.error = action.payload.error;
      }
    },
    groupOutgoingRemoved(
      state,
      action: PayloadAction<{ groupId: string; deliveryId: string }>,
    ) {
      const list = state.groupOutgoing[action.payload.groupId];
      if (list === undefined) return;
      state.groupOutgoing[action.payload.groupId] = list.filter((item) => item.deliveryId !== action.payload.deliveryId);
    },
    groupOutgoingHydrated(
      state,
      action: PayloadAction<{ groupId: string; messages: OutgoingGroupMessage[] }>,
    ) {
      const list = (state.groupOutgoing[action.payload.groupId] ??= []);
      const present = new Set(list.map((item) => item.deliveryId));
      for (const message of action.payload.messages) {
        if (!present.has(message.deliveryId)) {
          list.push(message);
        }
      }
    },
  },
});

export const {
  connected,
  disconnected,
  viewing,
  bindOwner,
  clearLiveOutput,
  controlNotice,
  sessionActivity,
  sessionFailed,
  previewStarted,
  previewDelta,
  previewAborted,
  thinkingStarted,
  thinkingDelta,
  thinkingClosed,
  outputFinal,
  toolProgress,
  conversationStatus,
  outgoingQueued,
  outgoingConfirmed,
  outgoingFailed,
  outgoingRemoved,
  outgoingHydrated,
  groupOutgoingQueued,
  groupOutgoingConfirmed,
  groupOutgoingFailed,
  groupOutgoingRemoved,
  groupOutgoingHydrated,
} = runtimeSlice.actions;
export default runtimeSlice.reducer;
