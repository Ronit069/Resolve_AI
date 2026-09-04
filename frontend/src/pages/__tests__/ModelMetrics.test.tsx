import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelMetrics } from "../ModelMetrics";
import { getModelEvaluation, ApiError } from "../../api/client";
import type { ModelEvaluationResponse } from "../../api/types";

vi.mock("../../api/client", () => ({
  getModelEvaluation: vi.fn(),
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

const SAMPLE_EVALUATION: ModelEvaluationResponse = {
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
};

describe("ModelMetrics (Phase 2)", () => {
  it("shows a loading state", () => {
    vi.mocked(getModelEvaluation).mockReturnValue(new Promise(() => {}));
    render(<ModelMetrics />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });

  it("renders real precision/recall/F1/accuracy from the API response", async () => {
    vi.mocked(getModelEvaluation).mockResolvedValue(SAMPLE_EVALUATION);
    render(<ModelMetrics />);
    expect(await screen.findByText(/Precision: 100\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/Recall: 59\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/F1: 74\.2%/)).toBeInTheDocument();
    expect(screen.getByText(/Accuracy: 87\.1%/)).toBeInTheDocument();
  });

  it("renders the confusion matrix values", async () => {
    vi.mocked(getModelEvaluation).mockResolvedValue(SAMPLE_EVALUATION);
    render(<ModelMetrics />);
    await screen.findByText(/Confusion Matrix/);
    const table = screen.getByRole("table");
    expect(table).toHaveTextContent("964");
    expect(table).toHaveTextContent("262");
    expect(table).toHaveTextContent("182");
    // fp = 0 appears in the matrix cell, not just anywhere on the page.
    const rows = screen.getAllByRole("row");
    const negativeRow = rows.find((r) => r.textContent?.includes("Actual Negative"));
    expect(negativeRow).toHaveTextContent(/964\s*0/);
  });

  it("renders the decision distribution", async () => {
    vi.mocked(getModelEvaluation).mockResolvedValue(SAMPLE_EVALUATION);
    render(<ModelMetrics />);
    expect(await screen.findByText(/ACCEPT: 742/)).toBeInTheDocument();
    expect(screen.getByText(/REVIEW: 404/)).toBeInTheDocument();
    expect(screen.getByText(/CONTEST: 262/)).toBeInTheDocument();
    expect(screen.getByText(/Expected cost: 2020\.00/)).toBeInTheDocument();
  });

  it("renders model and evaluation provenance", async () => {
    vi.mocked(getModelEvaluation).mockResolvedValue(SAMPLE_EVALUATION);
    render(<ModelMetrics />);
    expect(await screen.findByText(/CatBoost \/ 20260904_135245_v1/)).toBeInTheDocument();
    expect(screen.getByText(/IsotonicRegression/)).toBeInTheDocument();
    expect(screen.getByText(/step14_threshold_policy_20260904_135311_v1/)).toBeInTheDocument();
  });

  it("communicates these are held-out, non-production metrics", async () => {
    vi.mocked(getModelEvaluation).mockResolvedValue(SAMPLE_EVALUATION);
    render(<ModelMetrics />);
    await screen.findByText(/Precision/);
    expect(screen.getByText(/not.*production prediction performance/i)).toBeInTheDocument();
  });

  it("does not show the old 'not yet available' placeholder when data is available", async () => {
    vi.mocked(getModelEvaluation).mockResolvedValue(SAMPLE_EVALUATION);
    render(<ModelMetrics />);
    await screen.findByText(/Precision/);
    expect(screen.queryByText(/Evaluation metrics are not yet available/i)).not.toBeInTheDocument();
  });

  it("renders an honest unavailable state when the artifact does not exist (503)", async () => {
    vi.mocked(getModelEvaluation).mockRejectedValue(
      new ApiError(503, "No authoritative Step 15 evaluation artifact found.")
    );
    render(<ModelMetrics />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/No authoritative Step 15 evaluation artifact found/i);
  });

  it("renders a 403 error state, preserving existing role-based error handling", async () => {
    vi.mocked(getModelEvaluation).mockRejectedValue(new ApiError(403, "User role not authorized for this action"));
    render(<ModelMetrics />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/not authorized/i);
  });
});
