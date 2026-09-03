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
    <div>
      <h1>Risk Command Center</h1>
      <AsyncState loading={loading} error={error} data={data} emptyMessage="No metrics available.">
        {(metrics) => (
          <div>
            <section>
              <h2>Queue Age</h2>
              <p>Active items: {metrics.queue_age.active_item_count}</p>
              <p>Average age (s): {metrics.queue_age.average_age_seconds ?? "—"}</p>
            </section>
            <section>
              <h2>Near Deadline</h2>
              <p>Near deadline ({metrics.near_deadline.threshold_hours}h): {metrics.near_deadline.near_deadline_count}</p>
              <p>Expired: {metrics.near_deadline.expired_count}</p>
            </section>
            <section>
              <h2>Review Turnaround</h2>
              <p>Completed items: {metrics.review_turnaround.completed_item_count}</p>
              <p>Average turnaround (s): {metrics.review_turnaround.average_turnaround_seconds ?? "—"}</p>
            </section>
            <Link to="/queue">Open Dispute Queue</Link>
          </div>
        )}
      </AsyncState>
    </div>
  );
}
