import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ResponseReview } from "../ResponseReview";
import { IdentityProvider } from "../../state/IdentityContext";
import { getCurrentDraft, submitReviewAction, generateDraft, ApiError } from "../../api/client";

vi.mock("../../api/client", () => ({
  getCurrentDraft: vi.fn(),
  submitReviewAction: vi.fn(),
  generateDraft: vi.fn(),
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
  vi.mocked(getCurrentDraft).mockResolvedValue({
    draft_id: "d1",
    case_id: "c1",
    guardrail_status: "PASS",
    summary: "Evidence supports contest.",
    contest_amount_minor: "10000",
    draft_json: {},
    created_at: "2026-09-01T00:00:00Z",
  });
});

function renderReview(role: string | null = "APPROVER") {
  if (role) {
    window.localStorage.setItem("resolveai.dev.userId", "u1");
    window.localStorage.setItem("resolveai.dev.roleLabel", role);
  }
  return render(
    <MemoryRouter initialEntries={["/cases/c1/review"]}>
      <IdentityProvider>
        <Routes>
          <Route path="/cases/:caseId/review" element={<ResponseReview />} />
        </Routes>
      </IdentityProvider>
    </MemoryRouter>
  );
}

async function submitForm() {
  fireEvent.click(screen.getByRole("button", { name: /submit/i }));
}

describe("ResponseReview", () => {
  it("renders the draft summary", async () => {
    renderReview();
    expect(await screen.findByText("Evidence supports contest.")).toBeInTheDocument();
  });

  it("renders the empty state message on 404 instead of a fatal error", async () => {
    vi.mocked(getCurrentDraft).mockRejectedValue(new ApiError(404, "No draft found for this case"));
    renderReview();
    expect(await screen.findByText("No draft generated for this case yet.")).toBeInTheDocument();
    expect(screen.queryByText("Not found.")).not.toBeInTheDocument();
  });

  it("calls generateDraft and refreshes when 'Generate AI Draft' is clicked", async () => {
    // Start with empty state
    vi.mocked(getCurrentDraft).mockRejectedValueOnce(new ApiError(404, "No draft"));
    vi.mocked(generateDraft).mockResolvedValueOnce({
      draft_id: "dnew", case_id: "c1", guardrail_status: "PASS", summary: "New generated summary",
      contest_amount_minor: "10000", draft_json: {}, created_at: "2026-09-02T00:00:00Z",
    });
    // Second call returns the populated draft
    vi.mocked(getCurrentDraft).mockResolvedValueOnce({
      draft_id: "dnew", case_id: "c1", guardrail_status: "PASS", summary: "New generated summary",
      contest_amount_minor: "10000", draft_json: {}, created_at: "2026-09-02T00:00:00Z",
    });

    renderReview();
    
    // Wait for the empty state
    const btn = await screen.findByRole("button", { name: "Generate AI Draft" });
    expect(btn).toBeInTheDocument();

    fireEvent.click(btn);

    // Verify it updates state and shows new summary
    expect(generateDraft).toHaveBeenCalledWith("c1");
    expect(await screen.findByText("New generated summary")).toBeInTheDocument();
  });

  it("hides the submit form for a non-APPROVER role (RoleGate is UX-only)", async () => {
    renderReview("RISK_ANALYST");
    await screen.findByText("Evidence supports contest.");
    expect(screen.queryByRole("button", { name: /submit/i })).not.toBeInTheDocument();
    expect(screen.getByText(/cannot submit review actions/i)).toBeInTheDocument();
  });

  it("shows an 'awaiting second approver' state, not a final state, on AWAITING_SECOND_APPROVAL", async () => {
    vi.mocked(submitReviewAction).mockResolvedValue({
      id: "ra1", queue_item_id: "q1", case_id: "c1", reviewer_id: "u1",
      action: "APPROVE_CONTEST", override_reason_code: null, notes: null,
      created_at: "2026-09-01T00:00:00Z", dual_approval_status: "AWAITING_SECOND_APPROVAL",
    });
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByText(/awaiting a second, distinct approver/i)).toBeInTheDocument();
  });

  it("shows a finalized state on FINALIZED", async () => {
    vi.mocked(submitReviewAction).mockResolvedValue({
      id: "ra1", queue_item_id: "q1", case_id: "c1", reviewer_id: "u1",
      action: "APPROVE_CONTEST", override_reason_code: null, notes: null,
      created_at: "2026-09-01T00:00:00Z", dual_approval_status: "FINALIZED",
    });
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByText("Review action finalized.")).toBeInTheDocument();
  });

  it("shows a finalized state on a single-approval (null dual_approval_status) response", async () => {
    vi.mocked(submitReviewAction).mockResolvedValue({
      id: "ra1", queue_item_id: "q1", case_id: "c1", reviewer_id: "u1",
      action: "REQUEST_MORE_EVIDENCE", override_reason_code: null, notes: null,
      created_at: "2026-09-01T00:00:00Z", dual_approval_status: null,
    });
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByText("Review action finalized.")).toBeInTheDocument();
  });

  it("shows a distinct escalated state on ESCALATED_CANCELLED", async () => {
    vi.mocked(submitReviewAction).mockResolvedValue({
      id: "ra1", queue_item_id: "q1", case_id: "c1", reviewer_id: "u1",
      action: "ESCALATE", override_reason_code: null, notes: null,
      created_at: "2026-09-01T00:00:00Z", dual_approval_status: "ESCALATED_CANCELLED",
    });
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByText(/pending decision was cancelled, not approved/i)).toBeInTheDocument();
  });

  it("surfaces the server's mismatch rejection verbatim", async () => {
    vi.mocked(submitReviewAction).mockRejectedValue(
      new ApiError(400, "Case is pending second approval; only a matching confirmation of the pending action or ESCALATE is accepted")
    );
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent(/only a matching confirmation/i);
  });

  it("surfaces the server's same-reviewer rejection verbatim", async () => {
    vi.mocked(submitReviewAction).mockRejectedValue(
      new ApiError(400, "Second approval must come from a different active APPROVER than the first")
    );
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent(/different active APPROVER/i);
  });

  it("surfaces the server's DONE-blocked rejection verbatim", async () => {
    vi.mocked(submitReviewAction).mockRejectedValue(new ApiError(400, "Queue item is already DONE"));
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent(/already DONE/i);
  });

  it("surfaces the server's override-required rejection verbatim", async () => {
    vi.mocked(submitReviewAction).mockRejectedValue(
      new ApiError(400, "override_reason_code and notes are required when contradicting ML recommendation or overriding a hard block")
    );
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent(/override_reason_code and notes are required/i);
  });

  it("surfaces a generic 403 from the server", async () => {
    vi.mocked(submitReviewAction).mockRejectedValue(new ApiError(403, "User role not authorized for this action"));
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent(/not authorized/i);
  });

  it("surfaces a generic 404 from the server", async () => {
    vi.mocked(submitReviewAction).mockRejectedValue(new ApiError(404, "Case not found"));
    renderReview();
    await screen.findByText("Evidence supports contest.");
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent(/case not found/i);
  });

  it("sends the selected action, override reason and notes to submitReviewAction", async () => {
    vi.mocked(submitReviewAction).mockResolvedValue({
      id: "ra1", queue_item_id: "q1", case_id: "c1", reviewer_id: "u1",
      action: "REJECT_RECOMMENDATION", override_reason_code: "OTHER", notes: "n",
      created_at: "2026-09-01T00:00:00Z", dual_approval_status: null,
    });
    renderReview();
    await screen.findByText("Evidence supports contest.");
    fireEvent.change(screen.getByLabelText(/Review action/i), { target: { value: "REJECT_RECOMMENDATION" } });
    fireEvent.change(screen.getByLabelText(/Override reason code/i), { target: { value: "OTHER" } });
    fireEvent.change(screen.getByLabelText(/Notes/i), { target: { value: "n" } });
    await submitForm();
    await waitFor(() =>
      expect(submitReviewAction).toHaveBeenCalledWith("c1", {
        action: "REJECT_RECOMMENDATION",
        override_reason_code: "OTHER",
        notes: "n",
      })
    );
  });
});
