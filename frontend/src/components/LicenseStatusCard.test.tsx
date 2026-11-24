import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LicenseStatusCard } from "./LicenseStatusCard";

afterEach(() => {
  cleanup();
});

describe("LicenseStatusCard", () => {
  it("renders active commercial license details and seat usage", () => {
    render(
      <LicenseStatusCard
        activeUserCount={4}
        license={{
          valid: true,
          mode: "licensed",
          customer: "Acme Quant Desk",
          tier: "pro",
          maxUsers: 10,
          expiresAt: "2027-11-24T00:00:00+00:00",
          reason: null,
        }}
      />,
    );

    expect(screen.getByText("Commercial License & Plan")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("Acme Quant Desk")).toBeInTheDocument();
    expect(screen.getByText("4 / 10 seats")).toBeInTheDocument();
  });

  it("renders alert banner when license is expired or invalid", () => {
    render(
      <LicenseStatusCard
        activeUserCount={2}
        license={{
          valid: false,
          mode: "expired",
          customer: "Expired Fund",
          tier: "starter",
          maxUsers: 3,
          expiresAt: "2026-01-01T00:00:00+00:00",
          reason: "License key has expired",
        }}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("License key has expired");
    expect(screen.getByText("EXPIRED")).toBeInTheDocument();
  });
});
