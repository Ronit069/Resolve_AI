import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CaseWorkspace } from "../CaseWorkspace";
import { getCaseWorkspace, ApiError } from "../../api/client";

vi.mock("../../api/client", () => ({
  getCaseWorkspace: vi.fn(),
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

function renderWorkspace(caseId = "c1") {
  return render(
    <MemoryRouter initialEntries={[`/cases/${caseId}`]}>
      <Routes>
        <Route path="/cases/:caseId" element={<CaseWorkspace />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("CaseWorkspace", () => {
  it("renders workspace data from GET .../workspace only", async () => {
    vi.mocked(getCaseWorkspace).mockResolvedValue({
      case: { case_id: "c1", merchant_id: "m1", processing_state: "REVIEW_PENDING" },
      dispute: { amount_minor: 20000, reason_code: "fraud", status: "open", respond_by: "2026-09-10T00:00:00Z" },
      queue_item: null,
      risk_prediction: {
        prediction_id: "p1",
        calibrated_probability: 0.8,
        recommendation: "CONTEST",
        hard_block: false,
        explanations: [],
      },
      evidence_documents: [],
      uncertainty_warnings: [],
    });
    renderWorkspace();
    expect(await screen.findByText("Reason: fraud")).toBeInTheDocument();
    expect(getCaseWorkspace).toHaveBeenCalledWith("c1");
    expect(getCaseWorkspace).toHaveBeenCalledTimes(1);
  });

  it("renders a 404 error state", async () => {
    vi.mocked(getCaseWorkspace).mockRejectedValue(new ApiError(404, "Case not found"));
    renderWorkspace();
    expect(await screen.findByRole("alert")).toHaveTextContent(/not found/i);
  });
});
