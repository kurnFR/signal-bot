import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AuthGate } from "./AuthGate";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("expired session during forced password change", () => {
  it("clears the stale token and returns to sign-in with the session-expired message", async () => {
    window.localStorage.setItem("token", "expired-session-token");
    const profile = {
      id: 4,
      username: "trader01",
      email: null,
      role: "trader",
      must_change_password: true,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(profile), {
        status: 200,
        headers: { "content-type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Not authenticated" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    expect(await screen.findByRole("heading", { name: "Change your password" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "old-password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new-password-123" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "new-password-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has expired. Sign in again to continue.");
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
  });
  
  it("clears all password fields before a user signs in again after session expiry", async () => {
    window.localStorage.setItem("token", "expired-session-token");
    const profile = {
      id: 4,
      username: "trader01",
      email: null,
      role: "trader",
      must_change_password: true,
    };
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json(profile))
      .mockResolvedValueOnce(json({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(json({ token: "fresh-session-token", user: profile }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    expect(await screen.findByRole("heading", { name: "Change your password" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "old-password-secret" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new-password-secret-123" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "new-password-secret-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "trader01" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "login-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Change your password" })).toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expect(screen.getByLabelText("Confirm new password")).toHaveValue("");
  });
});
