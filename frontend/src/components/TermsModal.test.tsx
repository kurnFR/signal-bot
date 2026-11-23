import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { TermsModal } from "./TermsModal";
import * as api from "../lib/api";

describe("TermsModal component", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders nothing when isOpen is false", () => {
    const { container } = render(<TermsModal isOpen={false} onAccepted={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders compliance modal and requires acknowledgment checkbox before accepting", async () => {
    const onAccepted = vi.fn();
    const apiSpy = vi.spyOn(api, "apiRequest").mockResolvedValueOnce({ status: "success" });

    render(<TermsModal isOpen={true} onAccepted={onAccepted} />);

    expect(screen.getByText("Legal Risk Disclaimer & Compliance Terms")).toBeInTheDocument();
    const acceptBtn = screen.getByRole("button", { name: "Acknowledge & Enter Workstation" });
    expect(acceptBtn).toBeDisabled();

    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(acceptBtn).toBeEnabled();

    fireEvent.click(acceptBtn);

    await waitFor(() => {
      expect(apiSpy).toHaveBeenCalledWith("/api/auth/accept-terms", { method: "POST" });
      expect(onAccepted).toHaveBeenCalledTimes(1);
    });

    apiSpy.mockRestore();
  });
});
