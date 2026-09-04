import { useEffect, useState } from "react";
import { getModelEvaluation } from "../api/client";
import type { ModelEvaluationResponse } from "../api/types";
import { AsyncState } from "../components/AsyncState";

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * I-06 — Model Metrics (Phase 2).
 *
 * Renders the authoritative Step 15 held-out evaluation artifact, fetched
 * from GET /api/v1/observability/model-evaluation. Every number below is
 * formatted directly from that API response — nothing here is
 * hard-coded. This is a one-time, locked, offline evaluation on a
 * held-out test set, not a live/production prediction-performance
 * dashboard.
 */
export function ModelMetrics() {
  const [data, setData] = useState<ModelEvaluationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getModelEvaluation()
      .then((res) => !cancelled && setData(res))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <h1>Model Metrics</h1>
      <AsyncState loading={loading} error={error} data={data} emptyMessage="No evaluation available.">
        {(evaluation) => (
          <div>
            <p role="note">
              Held-out, offline evaluation on a locked test set. These are not
              live operational metrics and do not represent production prediction
              performance.
            </p>

            <section>
              <h2>AI Model Evaluation</h2>
              <p>
                Held-out test set: {evaluation.sample_count} examples ({evaluation.positive_count}{" "}
                positive / {evaluation.negative_count} negative)
              </p>
              <p>Precision: {formatPercent(evaluation.precision)}</p>
              <p>Recall: {formatPercent(evaluation.recall)}</p>
              <p>F1: {formatPercent(evaluation.f1)}</p>
              <p>Accuracy: {formatPercent(evaluation.accuracy)}</p>
            </section>

            <section>
              <h2>Confusion Matrix</h2>
              <table>
                <thead>
                  <tr>
                    <th scope="col" />
                    <th scope="col">Predicted Negative</th>
                    <th scope="col">Predicted Positive</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">Actual Negative</th>
                    <td>{evaluation.confusion_matrix.tn}</td>
                    <td>{evaluation.confusion_matrix.fp}</td>
                  </tr>
                  <tr>
                    <th scope="row">Actual Positive</th>
                    <td>{evaluation.confusion_matrix.fn}</td>
                    <td>{evaluation.confusion_matrix.tp}</td>
                  </tr>
                </tbody>
              </table>
              <p>False positives: {evaluation.false_positive_count}</p>
            </section>

            <section>
              <h2>Decision Distribution</h2>
              <p>ACCEPT: {evaluation.accept_count}</p>
              <p>REVIEW: {evaluation.review_count}</p>
              <p>CONTEST: {evaluation.contest_count}</p>
              <p>Expected cost: {evaluation.expected_cost.toFixed(2)}</p>
            </section>

            <section>
              <h2>Calibration</h2>
              <p>Brier score (raw): {evaluation.brier_raw.toFixed(4)}</p>
              <p>Brier score (calibrated): {evaluation.brier_calibrated.toFixed(4)}</p>
            </section>

            <section>
              <h2>Provenance</h2>
              <p>
                Model: {evaluation.model.algorithm} / {evaluation.model.run_id}
              </p>
              <p>
                Evaluation: locked held-out test set, evaluated {evaluation.evaluation.evaluation_timestamp}
              </p>
              <p>Calibration method: {evaluation.evaluation.calibration_method ?? "—"}</p>
              <p>Policy: {evaluation.evaluation.policy_id}</p>
            </section>
          </div>
        )}
      </AsyncState>
    </div>
  );
}
