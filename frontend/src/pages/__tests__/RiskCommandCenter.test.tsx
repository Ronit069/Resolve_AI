import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { RiskCommandCenter } from "../RiskCommandCenter";
import { getQueueMetrics, ApiError } from "../../api/client";

vi.mock("../../api/client", () => ({
  getQueueMetrics: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

describe("RiskCommandCenter", () => {
  it("shows a loading state", () => {
    vi.mocked(getQueueMetrics).mockReturnValue(new Promise(() => {}));
    render(
      <MemoryRouter>
        <RiskCommandCenter />
      </MemoryRouter>
    );
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });

  it("renders metrics once loaded", async () => {
    vi.mocked(getQueueMetrics).mockResolvedValue({
      generated_at: "2026-01-01T00:00:00Z",
      queue_age: { active_item_count: 3, average_age_seconds: 120, min_age_seconds: 10, max_age_seconds: 300 },
      near_deadline: { threshold_hours: 24, near_deadline_count: 1, expired_count: 0 },
      review_turnaround: {
        completed_item_count: 2,
        average_turnaround_seconds: 60,
        min_turnaround_seconds: 30,
        max_turnaround_seconds: 90,
      },
    });
    render(
      <MemoryRouter>
        <RiskCommandCenter />
      </MemoryRouter>
    );
    expect(await screen.findByText(/Active items: 3/)).toBeInTheDocument();
    expect(screen.getByText(/Near deadline \(24h\): 1/)).toBeInTheDocument();
  });

  it("renders a 403 error state", async () => {
    vi.mocked(getQueueMetrics).mockRejectedValue(new ApiError(403, "User role not authorized for this action"));
    render(
      <MemoryRouter>
        <RiskCommandCenter />
      </MemoryRouter>
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(/not authorized/i);
  });
});
