import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { OnboardingWizard } from "./OnboardingWizard";

describe("OnboardingWizard component", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders nothing when isOpen is false", () => {
    const { container } = render(<OnboardingWizard isOpen={false} onComplete={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders step 1 and supports step navigation", () => {
    const onComplete = vi.fn();
    const onClose = vi.fn();

    render(<OnboardingWizard isOpen={true} onComplete={onComplete} onClose={onClose} />);

    expect(screen.getByText("First-Run Setup & Onboarding")).toBeInTheDocument();
    expect(screen.getByText("1. Secure Administrator Password")).toBeInTheDocument();

    const skipBtn = screen.getByRole("button", { name: "Skip / Already Configured" });
    fireEvent.click(skipBtn);

    expect(screen.getByText("2. Verify Core Engine Health")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Continue to Telegram Setup" }));
    expect(screen.getByText("3. Telegram Alerting (Optional)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("4. Deploy First Paper Strategy")).toBeInTheDocument();
  });

  it("calls onClose when close icon is clicked", () => {
    const onClose = vi.fn();
    render(<OnboardingWizard isOpen={true} onComplete={vi.fn()} onClose={onClose} />);

    const closeBtn = screen.getByLabelText("Close onboarding wizard");
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
