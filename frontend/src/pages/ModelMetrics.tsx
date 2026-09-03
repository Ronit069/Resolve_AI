/**
 * I-06 — Model Metrics.
 *
 * Deliberately UI-only for this implementation, per the frozen PO
 * decision: no metrics endpoint, no metrics table, no JSON persistence
 * convention, no filesystem dependency, and no fabricated/sample values.
 * This component makes ZERO network requests.
 */
export function ModelMetrics() {
  return (
    <div>
      <h1>Model Metrics</h1>
      <div role="status">
        Evaluation metrics are not yet available. The authoritative evaluation artifact
        will be provided by Module L (Deployment/MLOps/Observability).
      </div>
    </div>
  );
}
