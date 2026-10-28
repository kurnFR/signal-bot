import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PromotionStatusPanel } from "./PromotionStatusPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const policy = {
  enabled: false,
  min_test_trades: 30,
  min_profit_factor: 1.2,
  min_expectancy_r: 0.05,
  max_drawdown_r: 8,
  min_rank_score: 0.1,
  require_holdout: true,
  send_telegram: true,
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json" },
});

describe("read-only promotion status", () => {
  it("renders saved policy thresholds and an intentional empty audit state", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      return Promise.resolve(path.includes("promotion-policy")
        ? json({ policy })
        : json({ events: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PromotionStatusPanel />);

    expect(await screen.findByText("No promotion decisions have been recorded.")).toBeInTheDocument();
    expect(screen.getByText("DISABLED")).toBeInTheDocument();
    expect(screen.getByText("Enabled", { exact: true })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([, init]) => (init as RequestInit).method === "GET")).toBe(true);
  });

  it("recovers from a temporary API failure and displays reconciliation-required decisions", async () => {
    let policyCalls = 0;
    const event = {
      decision_hash: "decision-1",
      symbol: "BTCUSDT",
      market: "spot",
      timeframe: "1d",
      data_segment: "holdout",
      strategy_name: "trend_ema_v1",
      status: "reconciliation_required",
      reason: "Reconcile the activation result before retrying.",
      paper_config_id: null,
      telegram_result: "not_sent",
      created_at: "2026-10-28T18:00:00",
      updated_at: "2026-10-28T18:01:00",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      if (path.includes("promotion-policy")) {
        policyCalls += 1;
        return Promise.resolve(policyCalls === 1 ? json({ detail: "temporarily unavailable" }, 503) : json({ policy: { ...policy, enabled: true } }));
      }
      return Promise.resolve(json({ events: [event] }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PromotionStatusPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Promotion policy and audit could not be loaded.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("reconciliation required")).toBeInTheDocument();
    expect(screen.getByText("Reconcile the activation result before retrying.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.every(([, init]) => (init as RequestInit).method === "GET")).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
