import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { getCurrentDraft, generateDraft, submitReviewAction, ApiError } from "../api/client";
import type { DraftResponse, ReviewActionEnum, ReviewActionResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { RoleGate } from "../components/RoleGate";

const ACTIONS: ReviewActionEnum[] = [
  "APPROVE_CONTEST",
  "APPROVE_ACCEPT",
  "REQUEST_MORE_EVIDENCE",
  "EDIT_DRAFT",
  "REJECT_RECOMMENDATION",
  "ESCALATE",
];

type SubmitOutcome =
  | { kind: "awaiting-second-approval" }
  | { kind: "finalized" }
  | { kind: "escalated-cancelled" }
  | { kind: "error"; detail: string };

/**
 * I-05 — Response Review. The only screen in Module I that performs a
 * mutation, and it does so through the existing, unmodified H-03
 * POST .../review-action endpoint. Every branch below reads the server's
 * own response/error rather than re-implementing any of its rules —
 * see the Module I review-workflow contract.
 */
export function ResponseReview() {
  const { caseId } = useParams<{ caseId: string }>();
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [generating, setGenerating] = useState(false);

  const [action, setAction] = useState<ReviewActionEnum>("APPROVE_CONTEST");
  const [overrideReasonCode, setOverrideReasonCode] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<SubmitOutcome | null>(null);

  useEffect(() => {
    if (!caseId) return;
    let cancelled = false;
    setLoading(true);
    getCurrentDraft(caseId)
      .then((res) => !cancelled && setDraft(res))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  const handleGenerate = async () => {
    if (!caseId) return;
    setGenerating(true);
    setError(null);
    try {
      await generateDraft(caseId);
      const res = await getCurrentDraft(caseId);
      setDraft(res);
    } catch (err) {
      setError(err);
    } finally {
      setGenerating(false);
    }
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!caseId) return;
    setSubmitting(true);
    setOutcome(null);
    try {
      const response: ReviewActionResponse = await submitReviewAction(caseId, {
        action,
        override_reason_code: overrideReasonCode || undefined,
        notes: notes || undefined,
      });
      if (response.dual_approval_status === "AWAITING_SECOND_APPROVAL") {
        setOutcome({ kind: "awaiting-second-approval" });
      } else if (response.dual_approval_status === "ESCALATED_CANCELLED") {
        setOutcome({ kind: "escalated-cancelled" });
      } else {
        // "FINALIZED" or null (single-approval path) both mean the queue
        // item reached DONE — both are a final, complete state.
        setOutcome({ kind: "finalized" });
      }
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Failed to submit review action.";
      setOutcome({ kind: "error", detail });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Response Review</h1>
      </div>

      <div className="grid-2col">
        <div className="stack">
          <AsyncState
            loading={loading}
            error={error}
            data={draft}
            emptyMessage={
              <div className="card">
                <div className="empty-state">
                  <div className="empty-state-icon" aria-hidden="true">
                    ◇
                  </div>
                  <p>No draft generated for this case yet.</p>
                  <RoleGate allow={["APPROVER"]}>
                    <button onClick={handleGenerate} disabled={generating} className="btn btn-primary btn-block">
                      {generating ? (
                        <>
                          <span className="btn-spinner" aria-hidden="true" />
                          Generating...
                        </>
                      ) : (
                        <>
                          <span aria-hidden="true">✦ </span>
                          Generate AI Draft
                        </>
                      )}
                    </button>
                  </RoleGate>
                </div>
              </div>
            }
            treat404AsEmpty={true}
          >
            {(d) => (
              <section className="card">
                <div className="page-header" style={{ marginBottom: 12 }}>
                  <h2 style={{ marginBottom: 0 }}>AI Draft</h2>
                  <span
                    className={
                      d.guardrail_status === "PASS"
                        ? "badge badge-success badge-glow-success badge-lg"
                        : "badge badge-danger badge-glow-danger badge-lg"
                    }
                  >
                    {d.guardrail_status}
                  </span>
                </div>
                <div className="draft-doc">{d.summary}</div>
              </section>
            )}
          </AsyncState>
        </div>

        <div className="stack">
          {outcome?.kind === "awaiting-second-approval" && (
            <div role="status" className="state-banner">
              Submitted — awaiting a second, distinct approver. This is not yet final.
            </div>
          )}
          {outcome?.kind === "finalized" && (
            <div role="status" className="state-banner state-success">
              Review action finalized.
            </div>
          )}
          {outcome?.kind === "escalated-cancelled" && (
            <div role="status" className="state-banner">
              Escalated — the pending decision was cancelled, not approved.
            </div>
          )}
          {outcome?.kind === "error" && (
            <div role="alert" className="state-banner">
              {outcome.detail}
            </div>
          )}

          <RoleGate
            allow={["APPROVER"]}
            fallback={
              <p role="note" className="state-banner" style={{ display: "block" }}>
                Your current role cannot submit review actions.
              </p>
            }
          >
            <form onSubmit={handleSubmit} className="card">
              <h2>Review Action</h2>
              <div className="form-group">
                <label htmlFor="action-select" className="form-label">
                  Review action
                </label>
                <select
                  id="action-select"
                  className="form-select"
                  value={action}
                  onChange={(e) => setAction(e.target.value as ReviewActionEnum)}
                >
                  {ACTIONS.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="override-reason-input" className="form-label">
                  Override reason code (required by the server for some actions)
                </label>
                <input
                  id="override-reason-input"
                  className="form-input"
                  value={overrideReasonCode}
                  onChange={(e) => setOverrideReasonCode(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label htmlFor="notes-input" className="form-label">
                  Notes (required alongside an override reason)
                </label>
                <textarea
                  id="notes-input"
                  className="form-textarea"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </div>

              <button type="submit" disabled={submitting} className="btn btn-primary btn-block">
                {submitting ? <span className="btn-spinner" aria-hidden="true" /> : null} Submit
              </button>
            </form>
          </RoleGate>
        </div>
      </div>
    </div>
  );
}
