import { Navigate, Route, Routes, Link } from "react-router-dom";
import { IdentityProvider, useIdentity } from "./state/IdentityContext";
import { Login } from "./pages/Login";
import { RiskCommandCenter } from "./pages/RiskCommandCenter";
import { DisputeQueue } from "./pages/DisputeQueue";
import { CaseWorkspace } from "./pages/CaseWorkspace";
import { ResponseReview } from "./pages/ResponseReview";
import { ModelMetrics } from "./pages/ModelMetrics";
import { AuditActivity } from "./pages/AuditActivity";

function RequireIdentity({ children }: { children: JSX.Element }) {
  const { userId } = useIdentity();
  if (!userId) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function Layout({ children }: { children: JSX.Element }) {
  return (
    <div>
      <nav>
        <Link to="/">Risk Command Center</Link> | <Link to="/queue">Dispute Queue</Link> |{" "}
        <Link to="/metrics">Model Metrics</Link> | <Link to="/login">Switch identity</Link>
      </nav>
      {children}
    </div>
  );
}

export function App() {
  return (
    <IdentityProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <RequireIdentity>
              <Layout>
                <RiskCommandCenter />
              </Layout>
            </RequireIdentity>
          }
        />
        <Route
          path="/queue"
          element={
            <RequireIdentity>
              <Layout>
                <DisputeQueue />
              </Layout>
            </RequireIdentity>
          }
        />
        <Route
          path="/cases/:caseId"
          element={
            <RequireIdentity>
              <Layout>
                <CaseWorkspace />
              </Layout>
            </RequireIdentity>
          }
        />
        <Route
          path="/cases/:caseId/review"
          element={
            <RequireIdentity>
              <Layout>
                <ResponseReview />
              </Layout>
            </RequireIdentity>
          }
        />
        <Route
          path="/metrics"
          element={
            <RequireIdentity>
              <Layout>
                <ModelMetrics />
              </Layout>
            </RequireIdentity>
          }
        />
        <Route
          path="/cases/:caseId/audit"
          element={
            <RequireIdentity>
              <Layout>
                <AuditActivity />
              </Layout>
            </RequireIdentity>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </IdentityProvider>
  );
}
