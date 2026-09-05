import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useIdentity } from "../state/IdentityContext";
import type { AppUserRole } from "../api/types";

const ROLE_OPTIONS: AppUserRole[] = [
  "MERCHANT_ADMIN",
  "RISK_ANALYST",
  "APPROVER",
  "SYSTEM_WORKER",
  "MODEL_MAINTAINER",
];

/**
 * I-01 — development identity selector.
 *
 * This screen is explicitly NOT a login. It does not verify a password or
 * issue a token; it only records a user_id (sent as the X-User-Id header
 * on every request) and a role label used purely for client-side UI
 * gating (RoleGate). The server independently and authoritatively
 * verifies both identity and role on every request regardless of what is
 * selected here.
 */
export function Login() {
  const { userId, roleLabel, setIdentity } = useIdentity();
  const [userIdInput, setUserIdInput] = useState(userId ?? "");
  const [roleInput, setRoleInput] = useState<AppUserRole>(roleLabel ?? "APPROVER");
  const navigate = useNavigate();

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!userIdInput.trim()) return;
    setIdentity(userIdInput.trim(), roleInput);
    navigate("/");
  };

  return (
    <div className="page" style={{ maxWidth: 480, marginTop: "10vh" }}>
      <div className="card">
        <h1 className="page-title" style={{ fontSize: 22 }}>
          ResolveAI — Development Sign-in
        </h1>
        <p role="note" className="state-banner" style={{ display: "block" }}>
          <strong style={{ color: "var(--text)" }}>
            Development identity selector — not a secure login.
          </strong>{" "}
          This does not authenticate you. It only sets the identifier sent as the{" "}
          <code>X-User-Id</code> header on API requests, and a role label used only to
          show/hide UI controls. The server independently verifies your real role and
          access on every request.
        </p>
        <form onSubmit={handleSubmit}>
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
          <button type="submit" className="btn btn-primary btn-block">
            Continue
          </button>
        </form>
      </div>
    </div>
  );
}
