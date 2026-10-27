import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { getJson } from "./lib/api";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("React authentication gate", () => {
  it("signs in through the existing API and persists the returned bearer token", async () => {
    const user = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ token: "issued-session-token", user }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><div>Authenticated dashboard</div></AuthGate>);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "trader01" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Authenticated dashboard")).toBeInTheDocument();
    expect(window.localStorage.getItem("token")).toBe("issued-session-token");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", expect.objectContaining({ method: "POST", credentials: "same-origin" }));
  });
  it("requires password change before rendering protected children", async () => {
    window.localStorage.setItem("token", "existing-session-token");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: true };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(profile), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    expect(await screen.findByRole("heading", { name: "Change your password" })).toBeInTheDocument();
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
  });
  it("validates password confirmation before sending a password-change request", async () => {
    window.localStorage.setItem("token", "temporary-session-token");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: true };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(profile), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    expect(await screen.findByRole("heading", { name: "Change your password" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "temporary-password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new-password-123" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "different-password-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("New password and confirmation do not match.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
  });
  it("unlocks the dashboard only after the server confirms a forced password change", async () => {
    window.localStorage.setItem("token", "temporary-session-token");
    const temporaryProfile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: true };
    const activeProfile = { ...temporaryProfile, must_change_password: false };
    const json = (body: unknown) => new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json(temporaryProfile))
      .mockResolvedValueOnce(json({}))
      .mockResolvedValueOnce(json(activeProfile));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    expect(await screen.findByRole("heading", { name: "Change your password" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "temporary-password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new-password-123" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "new-password-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Protected dashboard")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/auth/change-password", expect.objectContaining({
      method: "POST",
      credentials: "same-origin",
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/auth/me", expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
    }));
  });
  it("clears an expired stored session and returns to sign in", async () => {
    window.localStorage.setItem("token", "expired-session-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Not authenticated" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/me", expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
    }));
  });
  it("preserves the stored session when session validation has a server error", async () => {
    window.localStorage.setItem("token", "possibly-valid-session-token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Service unavailable" }), {
      status: 503,
      headers: { "content-type": "application/json" },
    })));
    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Unable to verify your session/i);
    expect(window.localStorage.getItem("token")).toBe("possibly-valid-session-token");
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
  });
  it("shows server login errors accessibly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Invalid username or password" }), { status: 401, headers: { "content-type": "application/json" } })));
    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "wrong-user" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password");
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
  });
  it("returns an active dashboard to sign-in when a later API request receives 401", async () => {
    const user = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const loginResponse = new Response(JSON.stringify({ token: "active-session-token", user }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const expiredResponse = new Response(JSON.stringify({ detail: "Not authenticated" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
    const fetchMock = vi.fn().mockResolvedValueOnce(loginResponse).mockResolvedValueOnce(expiredResponse);
    vi.stubGlobal("fetch", fetchMock);

    function ProtectedContent() {
      return <button onClick={() => void getJson("/api/paper/positions").catch(() => undefined)}>Load protected data</button>;
    }

    render(<AuthGate><ProtectedContent /></AuthGate>);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "trader01" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("button", { name: "Load protected data" })).toBeInTheDocument();
    expect(window.localStorage.getItem("token")).toBe("active-session-token");

    fireEvent.click(screen.getByRole("button", { name: "Load protected data" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has expired. Sign in again to continue.");
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(screen.queryByRole("button", { name: "Load protected data" })).not.toBeInTheDocument();
  });

});
