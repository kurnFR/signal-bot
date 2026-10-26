import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AuthGate } from "./AuthGate";

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
  it("shows server login errors accessibly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Invalid username or password" }), { status: 401, headers: { "content-type": "application/json" } })));
    render(<AuthGate><div>Protected dashboard</div></AuthGate>);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "wrong-user" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password");
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
  });
});
