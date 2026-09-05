import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getCaseAuditLog } from "../api/client";
import type { AuditFeedResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";

function eventTypeBadgeClass(eventType: string): string {
  return eventType === "REVIEW_ACTION" ? "badge badge-indigo" : "badge badge-gray";
}

/** I-07 — Audit / Activity. Read-only; renders the merged AuditLog + ReviewAction feed. */
export function AuditActivity() {
  const { caseId } = useParams<{ caseId: string }>();
  const [data, setData] = useState<AuditFeedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!caseId) return;
    let cancelled = false;
    setLoading(true);
    getCaseAuditLog(caseId)
      .then((res) => !cancelled && setData(res))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Audit / Activity</h1>
      </div>
      <AsyncState
        loading={loading}
        error={error}
        data={data}
        isEmpty={(d) => d.items.length === 0}
        emptyMessage="No activity recorded for this case."
      >
        {(feed) => (
          <div className="card">
            <ul>
              {feed.items.map((event) => (
                <li key={`${event.event_type}:${event.event_id}`} className="card-row">
                  <span className={eventTypeBadgeClass(event.event_type)}>[{event.event_type}]</span>{" "}
                  {event.action}
                  {event.details ? ` — ${event.details}` : ""}
                  {" — "}
                  <span className="text-muted">{new Date(event.created_at).toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </AsyncState>
    </div>
  );
}
