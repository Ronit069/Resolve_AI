import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useIdentity } from "../state/IdentityContext";
import { login, ApiError } from "../api/client";
import type { AppUserRole } from "../api/types";

const ROLE_OPTIONS: AppUserRole[] = [
  "MERCHANT_ADMIN",
  "RISK_ANALYST",
  "APPROVER",
  "SYSTEM_WORKER",
  "MODEL_MAINTAINER",
];

/**
 * I-01 — Sign-in.
 *
 * Two paths, both real to the backend:
 *
 *  - Sign in (default): POST /api/v1/auth/login with email + password.
 *    Verified server-side (bcrypt + JWT, app/core/security.py). The
 *    returned access token is sent as Authorization: Bearer <token> on
 *    every subsequent request.
 *
 *  - Development identity selector (secondary, collapsed by default):
 *    the original X-User-Id mechanism. It does not verify a password or
 *    issue a token — it only records a user_id, sent as the X-User-Id
 *    header. The backend hard-disables that header whenever its
 *    ENVIRONMENT is production-like; it exists here purely so this
 *    frontend still works against demo-seeded accounts that have no
 *    password set. The server independently and authoritatively
 *    verifies identity/role on every request regardless of which path
 *    was used to sign in.
 */
export function Login() {
  const { userId, roleLabel, setIdentity, setSession } = useIdentity();
  const navigate = useNavigate();

  const [showDevSelector, setShowDevSelector] = useState(false);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [signInError, setSignInError] = useState<string | null>(null);

  const [userIdInput, setUserIdInput] = useState(userId ?? "");
  const [roleInput, setRoleInput] = useState<AppUserRole>(roleLabel ?? "APPROVER");

  const handleSignIn = async (event: FormEvent) => {
    event.preventDefault();
    setSignInError(null);
    setSigningIn(true);
    try {
      const result = await login(email.trim(), password);
      setSession(result.access_token, result.user_id, result.role);
      navigate("/");
    } catch (err) {
      setSignInError(err instanceof ApiError ? err.detail : "Sign-in failed. Please try again.");
    } finally {
      setSigningIn(false);
    }
  };

  const handleDevSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!userIdInput.trim()) return;
    setIdentity(userIdInput.trim(), roleInput);
    navigate("/");
  };

  return (
    <div className="page" style={{ maxWidth: 480, marginTop: "10vh" }}>
      <div className="card">
        <h1 className="page-title" style={{ fontSize: 22 }}>
          ResolveAI — Sign in
        </h1>
        <form onSubmit={handleSignIn}>
          <div className="form-group">
            <label htmlFor="email-input" className="form-label">
              Email
            </label>
            <input
              id="email-input"
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@merchant.com"
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="password-input" className="form-label">
              Password
            </label>
            <input
              id="password-input"
              type="password"
              className="form-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {signInError && (
            <p role="alert" className="state-banner" style={{ display: "block" }}>
              {signInError}
            </p>
          )}
          <button type="submit" className="btn btn-primary btn-block" disabled={signingIn}>
            {signingIn ? (
              <>
                <span className="btn-spinner" aria-hidden="true" />
                Signing in...
              </>
            ) : (
              "Sign in"
            )}
          </button>
        </form>

        <button
          type="button"
          className="btn btn-ghost btn-block"
          style={{ marginTop: 16 }}
          onClick={() => setShowDevSelector((v) => !v)}
        >
          {showDevSelector ? "Hide development identity selector" : "Use development identity selector instead"}
        </button>

        {showDevSelector && (
          <>
            <p role="note" className="state-banner" style={{ display: "block", marginTop: 16 }}>
              <strong style={{ color: "var(--text)" }}>
                Development identity selector — not a secure login.
              </strong>{" "}
              This does not authenticate you. It only sets the identifier sent as the{" "}
              <code>X-User-Id</code> header on API requests, and a role label used only to
              show/hide UI controls. Disabled entirely on a production backend. The server
              independently verifies your real role and access on every request.
            </p>
            <form onSubmit={handleDevSubmit}>
              <div className="form-group">
                <label htmlFor="user-id-input" className="form-label">
                  User ID (UUID)
                </label>
                <input
                  id="user-id-input"
                  className="form-input mono"
                  value={userIdInput}
                  onChange={(e) => setUserIdInput(e.target.value)}
                  placeholder="00000000-0000-0000-0000-000000000000"
                />
              </div>
              <div className="form-group">
                <label htmlFor="role-select" className="form-label">
                  Role (display only — not sent to the server)
                </label>
                <select
                  id="role-select"
                  className="form-select"
                  value={roleInput}
                  onChange={(e) => setRoleInput(e.target.value as AppUserRole)}
                >
                  {ROLE_OPTIONS.map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </select>
              </div>
              <button type="submit" className="btn btn-block">
                Continue with dev identity
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
