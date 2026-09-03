import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { DisputeQueue } from "../DisputeQueue";
import { listReviewQueue } from "../../api/client";

vi.mock("../../api/client", () => ({
  listReviewQueue: vi.fn(),
}));

function renderQueue() {
  return render(
    <MemoryRouter>
      <DisputeQueue />
    </MemoryRouter>
  );
}

describe("DisputeQueue", () => {
  it("shows the empty state when there are no items", async () => {
    vi.mocked(listReviewQueue).mockResolvedValue({ items: [], total_count: 0, limit: 25, offset: 0 });
    renderQueue();
    expect(await screen.findByText(/No disputes in the queue/i)).toBeInTheDocument();
  });

  it("renders queue rows once loaded", async () => {
    vi.mocked(listReviewQueue).mockResolvedValue({
      items: [
        {
          case_id: "c1",
          queue_item_id: "q1",
          queue_status: "PENDING",
          priority_score: 50,
          respond_by: "2026-09-05T00:00:00Z",
          dispute_amount_minor: 10000,
          dispute_currency: "INR",
          dispute_reason_code: "fraud",
          dispute_status: "open",
          recommendation: "CONTEST",
          hard_block: false,
        },
      ],
      total_count: 1,
      limit: 25,
      offset: 0,
    });
    renderQueue();
    expect(await screen.findByText("fraud")).toBeInTheDocument();
    expect(screen.getByText("CONTEST")).toBeInTheDocument();
  });
});
