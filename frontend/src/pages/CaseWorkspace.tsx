import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getCaseWorkspace } from "../api/client";
import type { CaseWorkspaceResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";

function probabilityBarClass(p: number): string {
  if (p > 0.7) return "progress-fill progress-fill-high";
  if (p >= 0.4) return "progress-fill progress-fill-mid";
  return "progress-fill progress-fill-low";
}

function recommendationBadgeClass(rec: string): string {
  if (rec === "CONTEST") return "badge badge-indigo badge-lg";
  if (rec === "ACCEPT") return "badge badge-success badge-lg";
  if (rec === "REVIEW") return "badge badge-warning badge-lg";
  return "badge badge-gray badge-lg";
}

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
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Case Workspace</h1>
      </div>
      <AsyncState loading={loading} error={error} data={data}>
        {(workspace) => (
          <div className="grid-2col">
            <div className="stack">
              <section className="card">
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
                <section className="card">
                  <h2>Risk Assessment</h2>
                  <p>
                    Recommendation:{" "}
                    <span className={recommendationBadgeClass(workspace.risk_prediction.recommendation)}>
                      {workspace.risk_prediction.recommendation}
                    </span>
                  </p>
                  <p>Probability: {workspace.risk_prediction.calibrated_probability}</p>
                  <div className="progress-track">
                    <div
                      className={probabilityBarClass(workspace.risk_prediction.calibrated_probability)}
                      style={{ width: `${Math.round(workspace.risk_prediction.calibrated_probability * 100)}%` }}
                    />
                  </div>
                  <p>Hard block: {workspace.risk_prediction.hard_block ? "Yes" : "No"}</p>
                  <ul>
                    {workspace.risk_prediction.explanations.map((exp) => (
                      <li key={exp.explanation_id} className="card-row">
                        <span className="card-row-label">{exp.display_text ?? exp.feature_name}</span>
                        {exp.shap_value != null && (
                          <span className="card-row-value">{exp.shap_value.toFixed(3)}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
            <div className="stack">
              <section className="card">
                <h2>Evidence Documents</h2>
                {workspace.evidence_documents.length === 0 ? (
                  <div className="empty-state">
                    <div className="empty-state-icon" aria-hidden="true">
                      ◇
                    </div>
                    No evidence documents uploaded yet
                  </div>
                ) : (
                  <ul>
                    {workspace.evidence_documents.map((doc) => (
                      <li key={doc.document_id} className="card-row">
                        <span className="card-row-label">{doc.original_filename ?? doc.object_key}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              {workspace.uncertainty_warnings.length > 0 && (
                <section className="card">
                  <h2>Warnings</h2>
                  <ul>
                    {workspace.uncertainty_warnings.map((w, i) => (
                      <li key={i} role="alert" className="state-banner">
                        [{w.source}] {w.message}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              <Link to={`/cases/${workspace.case.case_id}/review`} className="btn btn-primary btn-block">
                Go to Response Review →
              </Link>
            </div>
          </div>
        )}
      </AsyncState>
    </div>
  );
}
