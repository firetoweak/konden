import { z } from "zod";

import type { OutgoingGroupMessage, OutgoingUserMessage } from "../../realtime/runtimeSlice";

const USER_PREFIX = "konden.failedOutgoing.session.";
const GROUP_PREFIX = "konden.failedOutgoing.group.";

const failedUserSchema = z.array(z.object({
  deliveryId: z.string().min(1),
  text: z.string(),
  artifactRefs: z.array(z.string()),
  images: z.array(z.string()),
  files: z.array(z.object({
    attachment_id: z.string(),
    name: z.string(),
    size: z.number(),
  })),
  createdAt: z.string(),
  error: z.string().nullable(),
}));

const failedGroupSchema = z.array(z.object({
  deliveryId: z.string().min(1),
  text: z.string(),
  mention: z.array(z.string()),
  createdAt: z.string(),
  error: z.string().nullable(),
}));

export function loadFailedUserMessages(sessionId: string): OutgoingUserMessage[] {
  const raw = localStorage.getItem(`${USER_PREFIX}${sessionId}`);
  if (raw === null) return [];
  try {
    return failedUserSchema.parse(JSON.parse(raw)).map((item) => ({
      ...item,
      status: "failed" as const,
    }));
  } catch {
    localStorage.removeItem(`${USER_PREFIX}${sessionId}`);
    return [];
  }
}

export function saveFailedUserMessages(sessionId: string, messages: readonly OutgoingUserMessage[]): void {
  const failed = messages
    .filter((item) => item.status === "failed")
    .map(({ status: _status, ...rest }) => rest);
  if (failed.length === 0) {
    localStorage.removeItem(`${USER_PREFIX}${sessionId}`);
    return;
  }
  localStorage.setItem(`${USER_PREFIX}${sessionId}`, JSON.stringify(failed));
}

export function upsertFailedUserMessage(sessionId: string, message: OutgoingUserMessage): void {
  const current = loadFailedUserMessages(sessionId);
  const next = current.some((item) => item.deliveryId === message.deliveryId)
    ? current.map((item) => item.deliveryId === message.deliveryId ? { ...message, status: "failed" as const } : item)
    : [...current, { ...message, status: "failed" as const }];
  saveFailedUserMessages(sessionId, next);
}

export function removeFailedUserMessage(sessionId: string, deliveryId: string): void {
  const current = loadFailedUserMessages(sessionId);
  const next = current.filter((item) => item.deliveryId !== deliveryId);
  if (next.length !== current.length) {
    saveFailedUserMessages(sessionId, next);
  }
}

export function loadFailedGroupMessages(groupId: string): OutgoingGroupMessage[] {
  const raw = localStorage.getItem(`${GROUP_PREFIX}${groupId}`);
  if (raw === null) return [];
  try {
    return failedGroupSchema.parse(JSON.parse(raw)).map((item) => ({
      ...item,
      status: "failed" as const,
    }));
  } catch {
    localStorage.removeItem(`${GROUP_PREFIX}${groupId}`);
    return [];
  }
}

export function saveFailedGroupMessages(groupId: string, messages: readonly OutgoingGroupMessage[]): void {
  const failed = messages
    .filter((item) => item.status === "failed")
    .map(({ status: _status, ...rest }) => rest);
  if (failed.length === 0) {
    localStorage.removeItem(`${GROUP_PREFIX}${groupId}`);
    return;
  }
  localStorage.setItem(`${GROUP_PREFIX}${groupId}`, JSON.stringify(failed));
}

export function upsertFailedGroupMessage(groupId: string, message: OutgoingGroupMessage): void {
  const current = loadFailedGroupMessages(groupId);
  const next = current.some((item) => item.deliveryId === message.deliveryId)
    ? current.map((item) => item.deliveryId === message.deliveryId ? { ...message, status: "failed" as const } : item)
    : [...current, { ...message, status: "failed" as const }];
  saveFailedGroupMessages(groupId, next);
}

export function removeFailedGroupMessage(groupId: string, deliveryId: string): void {
  const current = loadFailedGroupMessages(groupId);
  const next = current.filter((item) => item.deliveryId !== deliveryId);
  if (next.length !== current.length) {
    saveFailedGroupMessages(groupId, next);
  }
}
