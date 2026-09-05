import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listReviewQueue } from "../api/client";
import type { QueueListingResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";

const PAGE_SIZE = 25;

function statusBadgeClass(status: string): string {
  if (status === "DONE") return "badge badge-success";
  if (status === "PENDING" || status === "PENDING_SECOND_APPROVAL") return "badge badge-warning";
  if (status === "ASSIGNED") return "badge badge-indigo";
  return "badge badge-gray";
}

function recommendationBadgeClass(rec: string | null): string {
  if (rec === "CONTEST") return "badge badge-indigo";
  if (rec === "ACCEPT") return "badge badge-success";
  if (rec === "REVIEW") return "badge badge-warning";
  return "badge badge-gray";
}

function isNearDeadline(respondBy: string): boolean {
  const diffMs = new Date(respondBy).getTime() - Date.now();
  return diffMs < 3 * 24 * 60 * 60 * 1000;
}

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
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dispute Queue</h1>
          <p className="page-subtitle">All open and recently resolved chargeback disputes.</p>
        </div>
        {data && <span className="count-chip">{data.total_count} total</span>}
      </div>
      <div className="form-row">
        <div className="form-group">
          <label htmlFor="status-filter" className="form-label">
            Status
          </label>
          <select
            id="status-filter"
            className="form-select"
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
        </div>
        <div className="form-group">
          <label htmlFor="sort-select" className="form-label">
            Sort
          </label>
          <select
            id="sort-select"
            className="form-select"
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
            <div className="table-wrap">
              <table className="data-table">
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
                      <td>
                        <span className="badge badge-gray">{item.dispute_reason_code}</span>
                      </td>
                      <td className="num-cell">
                        {(item.dispute_amount_minor / 100).toLocaleString("en-IN")} {item.dispute_currency}
                      </td>
                      <td>
                        <span className={statusBadgeClass(item.queue_status)}>{item.queue_status}</span>
                      </td>
                      <td className={isNearDeadline(item.respond_by) ? "text-danger" : undefined}>
                        {new Date(item.respond_by).toLocaleString()}
                      </td>
                      <td>
                        <span className={recommendationBadgeClass(item.recommendation)}>
                          {item.recommendation ?? "—"}
                        </span>
                      </td>
                      <td>
                        <Link to={`/cases/${item.case_id}`} className="btn btn-primary btn-sm">
                          Review →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="paginator">
              <button
                className="btn btn-ghost btn-sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                className="btn btn-ghost btn-sm"
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
