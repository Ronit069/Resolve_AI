import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getCaseWorkspace } from "../api/client";
import type { CaseWorkspaceResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";

/**
 * I-04 — Case Workspace. Read-only; sourced entirely from the existing,
 * tenant-safe GET /api/v1/cases/{case_id}/workspace endpoint (H-02).
 *
 * Deliberately does NOT call GET /{case_id}/evidence or
 * GET /{case_id}/document-intelligence — those endpoints were identified
 * during the Module I audit as lacking tenant isolation and must not be
 * consumed by this screen.
 */
export function CaseWorkspace() {
  const { caseId } = useParams<{ caseId: string }>();
  const [data, setData] = useState<CaseWorkspaceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!caseId) return;
    let cancelled = false;
    setLoading(true);
    getCaseWorkspace(caseId)
      .then((res) => !cancelled && setData(res))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  return (
    <div>
      <h1>Case Workspace</h1>
      <AsyncState loading={loading} error={error} data={data}>
        {(workspace) => (
          <div>
            <section>
              <h2>Dispute</h2>
              <p>Reason: {workspace.dispute.reason_code}</p>
              <p>Amount: {workspace.dispute.amount_minor / 100}</p>
              <p>Status: {workspace.dispute.status}</p>
              <p>
                Respond by:{" "}
                {workspace.dispute.respond_by ? new Date(workspace.dispute.respond_by).toLocaleString() : "—"}
              </p>
            </section>
            {workspace.risk_prediction && (
              <section>
                <h2>Risk Assessment</h2>
                <p>Recommendation: {workspace.risk_prediction.recommendation}</p>
                <p>Probability: {workspace.risk_prediction.calibrated_probability}</p>
                <p>Hard block: {workspace.risk_prediction.hard_block ? "Yes" : "No"}</p>
                <ul>
                  {workspace.risk_prediction.explanations.map((exp) => (
                    <li key={exp.explanation_id}>
                      {exp.display_text ?? exp.feature_name}
                      {exp.shap_value != null ? ` (${exp.shap_value.toFixed(3)})` : ""}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <section>
              <h2>Evidence Documents</h2>
              <ul>
                {workspace.evidence_documents.map((doc) => (
                  <li key={doc.document_id}>{doc.original_filename ?? doc.object_key}</li>
                ))}
              </ul>
            </section>
            {workspace.uncertainty_warnings.length > 0 && (
              <section>
                <h2>Warnings</h2>
                <ul>
                  {workspace.uncertainty_warnings.map((w, i) => (
                    <li key={i} role="alert">
                      [{w.source}] {w.message}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <Link to={`/cases/${workspace.case.case_id}/review`}>Go to Response Review</Link>
          </div>
        )}
      </AsyncState>
    </div>
  );
}
