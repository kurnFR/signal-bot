import { afterEach, describe, expect, it, vi } from "vitest";
import { parseTelegramDeliveryStatus, sendTelegramTestPing, TelegramActionError } from "./telegram";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function stubPayload(payload: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Telegram test result response contract", () => {
  it("accepts the backend's successful message-sent response", async () => {
    const fetchMock = stubPayload({
      success: true,
      bot: { username: "signal_test_bot", first_name: "Signal Bot" },
      chat_id: "-100123456789",
      error: null,
    });
    await expect(sendTelegramTestPing()).resolves.toMatchObject({
      success: true,
      bot: { username: "signal_test_bot", first_name: "Signal Bot" },
      chat_id: "-100123456789",
      error: null,
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/telegram/test", expect.objectContaining({ method: "POST" }));
  });

  it("accepts token-only success when no chat is configured", async () => {
    stubPayload({ success: true, bot: { username: "signal_test_bot" }, note: "Bot token verified, but TELEGRAM_CHAT_ID is missing" });
    await expect(sendTelegramTestPing()).resolves.toMatchObject({ success: true, note: expect.stringContaining("TELEGRAM_CHAT_ID") });
  });

  it.each([
    ["bot object", { success: true, bot: 5 }],
    ["bot username", { success: true, bot: { username: 5 } }],
    ["chat identifier", { success: true, chat_id: 123 }],
    ["error field", { success: false, error: 5 }],
    ["note field", { success: true, note: 5 }],
  ])("rejects malformed optional %s fields", async (_label, payload) => {
    stubPayload(payload);
    await expect(sendTelegramTestPing()).rejects.toMatchObject({
      name: "TelegramActionError",
      message: "Telegram test returned an invalid response",
    });
  });

  it("preserves the backend's HTTP error detail without treating it as a success payload", async () => {
    stubPayload({ detail: "TELEGRAM_BOT_TOKEN missing in .env" }, 400);
    await expect(sendTelegramTestPing()).rejects.toEqual(expect.objectContaining({
      name: "TelegramActionError",
      status: 400,
      message: "TELEGRAM_BOT_TOKEN missing in .env",
    } satisfies Partial<TelegramActionError>));
  });
});

describe("parseTelegramDeliveryStatus contract", () => {
  it("resolves boolean true and successful objects to 'success'", () => {
    expect(parseTelegramDeliveryStatus(true)).toEqual({ status: "success" });
    expect(parseTelegramDeliveryStatus({ success: true, message_id: 9988 })).toEqual({
      status: "success",
      messageId: 9988,
    });
    expect(parseTelegramDeliveryStatus({ success: true })).toEqual({
      status: "success",
      messageId: undefined,
    });
  });

  it("resolves boolean false and failed objects to 'failed' with error detail", () => {
    expect(parseTelegramDeliveryStatus(false)).toEqual({ status: "failed" });
    expect(parseTelegramDeliveryStatus({ success: false, error: "Telegram deployment alert failed" })).toEqual({
      status: "failed",
      error: "Telegram deployment alert failed",
    });
    expect(parseTelegramDeliveryStatus({ success: false })).toEqual({
      status: "failed",
      error: undefined,
    });
  });

  it("resolves null, undefined, empty object, and malformed inputs to 'unconfirmed'", () => {
    expect(parseTelegramDeliveryStatus(null)).toEqual({ status: "unconfirmed" });
    expect(parseTelegramDeliveryStatus(undefined)).toEqual({ status: "unconfirmed" });
    expect(parseTelegramDeliveryStatus({})).toEqual({ status: "unconfirmed" });
    expect(parseTelegramDeliveryStatus({ status: "ok" })).toEqual({ status: "unconfirmed" });
    expect(parseTelegramDeliveryStatus("success")).toEqual({ status: "unconfirmed" });
    expect(parseTelegramDeliveryStatus(123)).toEqual({ status: "unconfirmed" });
    expect(parseTelegramDeliveryStatus([])).toEqual({ status: "unconfirmed" });
  });
});
