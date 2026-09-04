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
    <div>
      <h1>Response Review</h1>
      <AsyncState 
        loading={loading} 
        error={error} 
        data={draft} 
        emptyMessage={
          <div>
            <p>No draft generated for this case yet.</p>
            <RoleGate allow={["APPROVER"]}>
              <button onClick={handleGenerate} disabled={generating}>
                {generating ? "Generating..." : "Generate AI Draft"}
              </button>
            </RoleGate>
          </div>
        }
        treat404AsEmpty={true}
      >
        {(d) => (
          <section>
            <h2>Draft Summary</h2>
            <p>Guardrail status: {d.guardrail_status}</p>
            <p>{d.summary}</p>
          </section>
        )}
      </AsyncState>

      {outcome?.kind === "awaiting-second-approval" && (
        <div role="status">
          Submitted — awaiting a second, distinct approver. This is not yet final.
        </div>
      )}
      {outcome?.kind === "finalized" && <div role="status">Review action finalized.</div>}
      {outcome?.kind === "escalated-cancelled" && (
        <div role="status">Escalated — the pending decision was cancelled, not approved.</div>
      )}
      {outcome?.kind === "error" && <div role="alert">{outcome.detail}</div>}

      <RoleGate
        allow={["APPROVER"]}
        fallback={<p role="note">Your current role cannot submit review actions.</p>}
      >
        <form onSubmit={handleSubmit}>
          <label htmlFor="action-select">Review action</label>
          <select
            id="action-select"
            value={action}
            onChange={(e) => setAction(e.target.value as ReviewActionEnum)}
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>

          <label htmlFor="override-reason-input">
            Override reason code (required by the server for some actions)
          </label>
          <input
            id="override-reason-input"
            value={overrideReasonCode}
            onChange={(e) => setOverrideReasonCode(e.target.value)}
          />

          <label htmlFor="notes-input">Notes (required alongside an override reason)</label>
          <textarea id="notes-input" value={notes} onChange={(e) => setNotes(e.target.value)} />

          <button type="submit" disabled={submitting}>
            Submit
          </button>
        </form>
      </RoleGate>
    </div>
  );
}
