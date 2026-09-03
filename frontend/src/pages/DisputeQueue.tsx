import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listReviewQueue } from "../api/client";
import type { QueueListingResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";

const PAGE_SIZE = 25;

/** I-03 — Dispute Queue. Read-only; filter/sort/paginate the new queue-listing endpoint. */
export function DisputeQueue() {
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("respond_by:asc");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<QueueListingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listReviewQueue({ status: status || undefined, sort, limit: PAGE_SIZE, offset })
      .then((res) => !cancelled && setData(res))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [status, sort, offset]);

  return (
    <div>
      <h1>Dispute Queue</h1>
      <div>
        <label htmlFor="status-filter">Status</label>
        <select
          id="status-filter"
          value={status}
          onChange={(e) => {
            setOffset(0);
            setStatus(e.target.value);
          }}
        >
          <option value="">All</option>
          <option value="PENDING">Pending</option>
          <option value="ASSIGNED">Assigned</option>
          <option value="PENDING_SECOND_APPROVAL">Pending second approval</option>
          <option value="DONE">Done</option>
        </select>
        <label htmlFor="sort-select">Sort</label>
        <select
          id="sort-select"
          value={sort}
          onChange={(e) => {
            setOffset(0);
            setSort(e.target.value);
          }}
        >
          <option value="respond_by:asc">Deadline (soonest first)</option>
          <option value="priority_score:desc">Priority (highest first)</option>
        </select>
      </div>
      <AsyncState
        loading={loading}
        error={error}
        data={data}
        isEmpty={(d) => d.items.length === 0}
        emptyMessage="No disputes in the queue."
      >
        {(listing) => (
          <>
            <table>
              <thead>
                <tr>
                  <th>Reason</th>
                  <th>Amount</th>
                  <th>Status</th>
                  <th>Deadline</th>
                  <th>Recommendation</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {listing.items.map((item) => (
                  <tr key={item.queue_item_id}>
                    <td>{item.dispute_reason_code}</td>
                    <td>
                      {item.dispute_amount_minor / 100} {item.dispute_currency}
                    </td>
                    <td>{item.queue_status}</td>
                    <td>{new Date(item.respond_by).toLocaleString()}</td>
                    <td>{item.recommendation ?? "—"}</td>
                    <td>
                      <Link to={`/cases/${item.case_id}`}>Open</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div>
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Previous
              </button>
              <button
                disabled={offset + PAGE_SIZE >= listing.total_count}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </button>
              <span>
                {offset + 1}-{Math.min(offset + PAGE_SIZE, listing.total_count)} of {listing.total_count}
              </span>
            </div>
          </>
        )}
      </AsyncState>
    </div>
  );
}
