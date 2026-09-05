import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getQueueMetrics } from "../api/client";
import type { ObservabilityMetricsResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";

/** I-02 — Risk Command Center. Read-only; renders the existing H-22 metrics endpoint. */
export function RiskCommandCenter() {
  const [data, setData] = useState<ObservabilityMetricsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getQueueMetrics()
      .then((res) => !cancelled && setData(res))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Risk Command Center</h1>
          <p className="page-subtitle">Live operational health of the review queue.</p>
        </div>
      </div>
      <AsyncState loading={loading} error={error} data={data} emptyMessage="No metrics available.">
        {(metrics) => (
          <div>
            <div className="kpi-grid">
              <div className="kpi-card">
                <div className="kpi-label">Active Items</div>
                <div className="kpi-value">{metrics.queue_age.active_item_count}</div>
                <div className="kpi-sub">Average age (s): {metrics.queue_age.average_age_seconds ?? "—"}</div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">Near Deadline ({metrics.near_deadline.threshold_hours}h)</div>
                <div className="kpi-value">{metrics.near_deadline.near_deadline_count}</div>
                <div className="kpi-sub">Expired: {metrics.near_deadline.expired_count}</div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">Review Turnaround</div>
                <div className="kpi-value">{metrics.review_turnaround.completed_item_count}</div>
                <div className="kpi-sub">
                  Average turnaround (s): {metrics.review_turnaround.average_turnaround_seconds ?? "—"}
                </div>
              </div>
            </div>

            <hr className="section-divider" />

            <section className="card">
              <h2>Queue Age</h2>
              <p>Active items: {metrics.queue_age.active_item_count}</p>
              <p>Average age (s): {metrics.queue_age.average_age_seconds ?? "—"}</p>
            </section>
            <section className="card">
              <h2>Near Deadline</h2>
              <p>
                Near deadline ({metrics.near_deadline.threshold_hours}h): {metrics.near_deadline.near_deadline_count}
              </p>
              <p>Expired: {metrics.near_deadline.expired_count}</p>
            </section>
            <section className="card">
              <h2>Review Turnaround</h2>
              <p>Completed items: {metrics.review_turnaround.completed_item_count}</p>
              <p>Average turnaround (s): {metrics.review_turnaround.average_turnaround_seconds ?? "—"}</p>
            </section>
            <Link to="/queue" className="btn btn-primary">
              Open Dispute Queue →
            </Link>
          </div>
        )}
      </AsyncState>
    </div>
  );
}
