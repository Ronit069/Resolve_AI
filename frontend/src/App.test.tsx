import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { App } from "./App";

vi.mock("./api/client", () => ({
  getQueueMetrics: vi.fn().mockResolvedValue({
    generated_at: "2026-01-01T00:00:00Z",
    queue_age: { active_item_count: 0, average_age_seconds: null, min_age_seconds: null, max_age_seconds: null },
    near_deadline: { threshold_hours: 24, near_deadline_count: 0, expired_count: 0 },
    review_turnaround: {
      completed_item_count: 0,
      average_turnaround_seconds: null,
      min_turnaround_seconds: null,
      max_turnaround_seconds: null,
    },
  }),
  listReviewQueue: vi.fn().mockResolvedValue({ items: [], total_count: 0, limit: 25, offset: 0 }),
  getCaseWorkspace: vi.fn(),
  getCurrentDraft: vi.fn(),
  submitReviewAction: vi.fn(),
  getCaseAuditLog: vi.fn(),
  setApiIdentity: vi.fn(),
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

beforeEach(() => {
  window.localStorage.clear();
});

describe("App routing", () => {
  it("redirects to /login when no identity is set", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    expect(await screen.findByText(/Development identity selector/i)).toBeInTheDocument();
  });

  it("renders the Risk Command Center at / once an identity is set", async () => {
    window.localStorage.setItem("resolveai.dev.userId", "11111111-1111-1111-1111-111111111111");
    window.localStorage.setItem("resolveai.dev.roleLabel", "APPROVER");
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    expect(await screen.findByRole("heading", { name: "Risk Command Center" })).toBeInTheDocument();
  });

  it("renders the Dispute Queue at /queue", async () => {
    window.localStorage.setItem("resolveai.dev.userId", "11111111-1111-1111-1111-111111111111");
    render(
      <MemoryRouter initialEntries={["/queue"]}>
        <App />
      </MemoryRouter>
    );
    expect(await screen.findByRole("heading", { name: "Dispute Queue" })).toBeInTheDocument();
  });

  it("renders Model Metrics at /metrics", async () => {
    window.localStorage.setItem("resolveai.dev.userId", "11111111-1111-1111-1111-111111111111");
    render(
      <MemoryRouter initialEntries={["/metrics"]}>
        <App />
      </MemoryRouter>
    );
    expect(await screen.findByRole("heading", { name: "Model Metrics" })).toBeInTheDocument();
  });
});
