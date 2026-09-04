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
  getModelEvaluation: vi.fn().mockResolvedValue({
    sample_count: 1408,
    positive_count: 444,
    negative_count: 964,
    precision: 1.0,
    recall: 0.5900900900900901,
    f1: 0.7422096317280453,
    accuracy: 0.8707386363636364,
    confusion_matrix: { tp: 262, tn: 964, fp: 0, fn: 182 },
    false_positive_count: 0,
    expected_cost: 2020.0,
    accept_count: 742,
    review_count: 404,
    contest_count: 262,
    brier_raw: 0.08203449135087003,
    brier_calibrated: 0.07149647116098277,
    model: {
      algorithm: "CatBoost",
      run_id: "20260904_135245_v1",
      model_sha256: "90a79898da06a8e529d08f9e8fe117d67f2133572d7327a3aff89eed00f04bcd",
    },
    evaluation: {
      holdout_file: "synthetic_benchmark_v1_test_holdout.jsonl",
      holdout_sha256: "fbd7f48920f51cd15b211bfa9b9b05109aba3fad598a9d512cf7f259319aafa5",
      evaluation_timestamp: "20260904_135631",
      calibration_method: "IsotonicRegression",
      policy_id: "step14_threshold_policy_20260904_135311_v1",
    },
  }),
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
