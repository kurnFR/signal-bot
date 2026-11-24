import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuditLogsSection } from "./AuditLogsSection";

const fetchAuditLogsMock = vi.fn();

vi.mock("../lib/users", () => ({
  fetchAuditLogs: (...args: unknown[]) => fetchAuditLogsMock(...args),
}));

afterEach(() => {
  cleanup();
  fetchAuditLogsMock.mockReset();
});

describe("AuditLogsSection", () => {
  it("renders fetched operator audit logs", async () => {
    fetchAuditLogsMock.mockResolvedValueOnce([
      {
        id: 101,
        user_id: 1,
        username: "admin",
        action: "TELEGRAM_CONFIG_UPDATE",
        details: { delivery_authorized: true },
        ip_address: "127.0.0.1",
        created_at: "2026-11-23 10:00:00",
      },
    ]);

    render(<AuditLogsSection />);

    await waitFor(() => {
      expect(screen.getByText("TELEGRAM_CONFIG_UPDATE")).toBeInTheDocument();
    });
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("1 EVENTS")).toBeInTheDocument();
  });

  it("shows an error alert when loading audit logs fails", async () => {
    fetchAuditLogsMock.mockRejectedValueOnce(new Error("Forbidden"));

    render(<AuditLogsSection />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Forbidden");
    });
  });
});
