import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuditActivity } from "../AuditActivity";
import { getCaseAuditLog } from "../../api/client";

vi.mock("../../api/client", () => ({
  getCaseAuditLog: vi.fn(),
}));

function renderFeed() {
  return render(
    <MemoryRouter initialEntries={["/cases/c1/audit"]}>
      <Routes>
        <Route path="/cases/:caseId/audit" element={<AuditActivity />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("AuditActivity", () => {
  it("shows the empty state when there is no activity", async () => {
    vi.mocked(getCaseAuditLog).mockResolvedValue({ items: [], total_count: 0, limit: 25, offset: 0 });
    renderFeed();
    expect(await screen.findByText(/No activity recorded/i)).toBeInTheDocument();
  });

  it("renders both event types with a visible type indicator, without requiring the caller to merge them", async () => {
    vi.mocked(getCaseAuditLog).mockResolvedValue({
      items: [
        {
          event_type: "REVIEW_ACTION",
          event_id: "r1",
          case_id: "c1",
          actor_user_id: "u1",
          action: "APPROVE_CONTEST",
          details: null,
          created_at: "2026-09-01T01:00:00Z",
        },
        {
          event_type: "AUDIT_LOG",
          event_id: "a1",
          case_id: "c1",
          actor_user_id: "u2",
          action: "DISPUTE_EVENT_INGESTED",
          details: "ingested",
          created_at: "2026-09-01T00:00:00Z",
        },
      ],
      total_count: 2,
      limit: 25,
      offset: 0,
    });
    renderFeed();
    expect(await screen.findByText(/\[REVIEW_ACTION\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[AUDIT_LOG\]/)).toBeInTheDocument();
    expect(getCaseAuditLog).toHaveBeenCalledWith("c1");
  });
});
