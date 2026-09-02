import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelMetrics } from "../ModelMetrics";

const fetchSpy = vi.fn();
vi.stubGlobal("fetch", fetchSpy);

describe("ModelMetrics (I-06)", () => {
  it("renders the honest empty state and makes zero network calls", () => {
    render(<ModelMetrics />);
    expect(
      screen.getByText(/Evaluation metrics are not yet available/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Module L/i)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("never renders a numeric-looking metric value", () => {
    render(<ModelMetrics />);
    expect(screen.queryByText(/precision/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/recall/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/PR-AUC/i)).not.toBeInTheDocument();
  });
});
