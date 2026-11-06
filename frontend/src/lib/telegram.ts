import { assertApiPath, getJson } from "./api";

export interface TelegramStatus {
  configured: boolean;
  chatId: string | null;
  hasToken: boolean;
}
export interface TelegramTestResult {
  success: boolean;
  bot?: { username?: string; first_name?: string };
  chat_id?: string | null;
  error?: string | null;
  note?: string;
}

export type TelegramDeliveryOutcome =
  | { status: "success"; messageId?: number | string }
  | { status: "failed"; error?: string }
  | { status: "unconfirmed" };

/** Parses a Telegram deployment alert response into a structured outcome. */
export function parseTelegramDeliveryStatus(raw: unknown): TelegramDeliveryOutcome {
  if (raw === true) {
    return { status: "success" };
  }
  if (raw === false) {
    return { status: "failed" };
  }
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const obj = raw as Record<string, unknown>;
    if (obj.success === true) {
      const msgId = typeof obj.message_id === "number" || typeof obj.message_id === "string" ? obj.message_id : undefined;
      return { status: "success", messageId: msgId };
    }
    if (obj.success === false) {
      const err = typeof obj.error === "string" && obj.error.trim().length > 0 ? obj.error.trim() : undefined;
      return { status: "failed", error: err };
    }
    return { status: "unconfirmed" };
  }
  return { status: "unconfirmed" };
}
export class TelegramActionError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "TelegramActionError";
  }
}
export async function fetchTelegramStatus(signal?: AbortSignal): Promise<TelegramStatus> {
  const status = await getJson<TelegramStatus>("/api/telegram/status", signal);
  if (!status || typeof status.configured !== "boolean" || typeof status.hasToken !== "boolean" ||
      !(typeof status.chatId === "string" || status.chatId === null)) {
    throw new Error("Telegram status returned an invalid response");
  }
  return status;
}
/** Sends a real test ping. Callers must gate this to admins and require explicit confirmation. */
export async function sendTelegramTestPing(): Promise<TelegramTestResult> {
  const path = "/api/telegram/test";
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  try {
    const token = window.localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // The server rejects the request if no session token is available.
  }
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers,
    body: JSON.stringify({}),
  });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }
  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
      ? payload.detail.slice(0, 300)
      : `Telegram test failed (${response.status})`;
    throw new TelegramActionError(response.status, detail);
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
      !("success" in payload) || typeof payload.success !== "boolean") {
    throw new TelegramActionError(response.status, "Telegram test returned an invalid response");
  }
  const result = payload as Record<string, unknown>;
  if (result.bot !== undefined && (
      !result.bot || typeof result.bot !== "object" || Array.isArray(result.bot) ||
      (("username" in result.bot) && typeof result.bot.username !== "string") ||
      (("first_name" in result.bot) && typeof result.bot.first_name !== "string"))) {
    throw new TelegramActionError(response.status, "Telegram test returned an invalid response");
  }
  if ((result.chat_id !== undefined && result.chat_id !== null && typeof result.chat_id !== "string") ||
      (result.error !== undefined && result.error !== null && typeof result.error !== "string") ||
      (result.note !== undefined && typeof result.note !== "string")) {
    throw new TelegramActionError(response.status, "Telegram test returned an invalid response");
  }
  return payload as TelegramTestResult;
}
