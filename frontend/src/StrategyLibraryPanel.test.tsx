import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { StrategyLibraryPanel } from "./StrategyLibraryPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const payloads: Record<string, unknown> = {
  "/api/custom-strategy/list": { strategies: [{ name: "rsi_volume_v1", displayName: "RSI + Volume", description: "Mean reversion", rationale: "Confirm with volume", longConditions: [{ indicator: "rsi", operator: "<", value: 30 }], shortConditions: [{ indicator: "rsi", operator: ">", value: 70 }], slAtrMult: 1.5, riskRewardRatio: 2.5, useTrailingStop: true }] },
  "/api/custom-strategy/indicators": { indicators: [{ name: "rsi", label: "RSI (14)", category: "Momentum", description: "Relative Strength Index", default: 30, type: "numeric" }], operators: ["<", "<=", ">", ">=", "=="] },
};
describe("read-only strategy library", () => {
  it("renders saved strategy rules and backend indicator metadata using GET only", async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => Promise.resolve(new Response(JSON.stringify(payloads[path]), { status: 200, headers: { "content-type": "application/json" } })));
    vi.stubGlobal("fetch", fetchMock);
    render(<StrategyLibraryPanel />);
    expect(await screen.findByRole("heading", { name: "RSI + Volume" })).toBeInTheDocument();
    expect(screen.getByText("Mean reversion")).toBeInTheDocument();
    expect(screen.getByText("rsi < 30")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Indicator catalog" })).toBeInTheDocument();
    expect(screen.getByText("RSI (14)")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });
  it("offers recoverable access errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Forbidden" }), { status: 403, headers: { "content-type": "application/json" } })));
    render(<StrategyLibraryPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your account cannot access the strategy library.");
  });
});
