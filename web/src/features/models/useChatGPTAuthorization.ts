import { skipToken } from "@reduxjs/toolkit/query";
import { useEffect, useState } from "react";
import {
  apiErrorDetail, kondenApi, useGetChatGPTAuthorizationQuery, useStartChatGPTAuthorizationMutation,
} from "../../api/kondenApi";
import { useAppDispatch } from "../../app/hooks";

export type ChatGPTAuthorization = {
  local: boolean;
  waiting: boolean;
  message: string | null;
  completed: string | null;
  start(): void;
};

export function useChatGPTAuthorization(connectionId: string | null): ChatGPTAuthorization {
  const dispatch = useAppDispatch();
  const [attempt, setAttempt] = useState<string | null>(null);
  const [completed, setCompleted] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [begin, { isLoading }] = useStartChatGPTAuthorizationMutation();
  const { currentData, error } = useGetChatGPTAuthorizationQuery(attempt ?? skipToken, {
    pollingInterval: 1000,
  });
  const local = window.location.protocol === "http:" &&
    ["127.0.0.1", "localhost"].includes(window.location.hostname);
  const waiting = isLoading || attempt !== null;

  useEffect(() => {
    if (attempt === null) return;
    if (error && "status" in error && error.status === 404) {
      setMessage(apiErrorDetail(error.data) ?? "无法确认本次授权，请查看连接状态。");
    } else if (currentData?.state === "connected") {
      setCompleted(attempt);
    } else if (currentData?.state === "failed") {
      setMessage(currentData.message);
    } else {
      return;
    }
    setAttempt(null);
    dispatch(kondenApi.util.invalidateTags(["ModelSettings"]));
  }, [attempt, currentData, dispatch, error]);

  async function start() {
    if (!connectionId || !local || waiting) return;
    setMessage(null);
    const tab = window.open("about:blank", "_blank");
    if (!tab) {
      setMessage("浏览器拦截了授权标签页，请允许打开后重试。");
      return;
    }
    tab.opener = null;
    try {
      const { authorization_id: id } = await begin(connectionId).unwrap();
      setAttempt(id);
      tab.location.replace(`${window.location.origin}/api/model-settings/chatgpt/authorization/${encodeURIComponent(id)}/open`);
    } catch (cause) {
      tab.close();
      setMessage(typeof cause === "string" ? cause : "无法打开 ChatGPT 授权，请重试。");
    }
  }

  return { local, waiting, message, completed, start };
}
