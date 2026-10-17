import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { setApiAccessToken, setApiIdentity } from "../api/client";
import type { AppUserRole } from "../api/types";

const USER_ID_KEY = "resolveai.dev.userId";
const ROLE_KEY = "resolveai.dev.roleLabel";
const ACCESS_TOKEN_KEY = "resolveai.accessToken";

interface IdentityContextValue {
  userId: string | null;
  /**
   * Purely a client-side display/UX-gating label. For a real session
   * (setSession, below) this is the role the server returned at login.
   * For the dev fallback (setIdentity) it's whatever the developer typed
   * in. Either way it is NEVER sent to the server and NEVER used as an
   * authorization claim — every gated backend call re-checks the real
   * role itself via require_role(). See RoleGate.tsx.
   */
  roleLabel: AppUserRole | null;
  /** Set once there's a real JWT (see api/client.ts::login) -- null while
   * only the dev X-User-Id fallback is active. */
  accessToken: string | null;
  /** Dev-only fallback identity (X-User-Id header) -- works against
   * demo-seeded accounts that have no password set. Clears any real
   * session, since the API client only ever sends one credential. */
  setIdentity: (userId: string | null, roleLabel: AppUserRole | null) => void;
  /** Real authenticated session from POST /api/v1/auth/login. */
  setSession: (accessToken: string, userId: string, roleLabel: AppUserRole) => void;
}

const IdentityContext = createContext<IdentityContextValue | undefined>(undefined);

/**
 * Development-only identity state.
 *
 * This is NOT authentication. It holds a plain user_id string that gets
 * attached as the X-User-Id header on every backend request. There is no
 * password, token, or cryptographic proof of identity anywhere in this
 * mechanism — see Login.tsx and the Module I blueprint's frozen PO
 * decision on authentication.
 */
export function IdentityProvider({ children }: { children: ReactNode }) {
  // Read (and synchronously mirror into the API client) during render, via
  // useState's lazy initializer — this runs once, before IdentityProvider
  // returns its element tree, so it happens before React ever renders (and
  // therefore before it can run the mount effect of) any descendant page
  // component. Doing this from a useEffect instead would run AFTER child
  // effects (React fires effects child-before-parent), letting a child's
  // own data-fetching effect race ahead of the API client's identity being
  // set — invisible in dev because React.StrictMode's double-invoke masks
  // it, but real in production. See D-01.
  // accessToken is read/applied first: if a real session exists, the dev
  // X-User-Id fallback below must never also get pushed into the API
  // client (it would be ignored anyway, since the client prefers a
  // token when both are set, but keeping exactly one source of truth
  // here avoids ever reasoning about "which one actually won").
  const [accessToken, setAccessTokenState] = useState<string | null>(() => {
    let initial: string | null = null;
    try {
      initial = window.localStorage.getItem(ACCESS_TOKEN_KEY);
    } catch {
      initial = null;
    }
    setApiAccessToken(initial);
    return initial;
  });
  const [userId, setUserIdState] = useState<string | null>(() => {
    let initial: string | null = null;
    try {
      initial = window.localStorage.getItem(USER_ID_KEY);
    } catch {
      initial = null;
    }
    if (!accessToken) {
      setApiIdentity(initial);
    }
    return initial;
  });
  const [roleLabel, setRoleLabelState] = useState<AppUserRole | null>(() => {
    try {
      return (window.localStorage.getItem(ROLE_KEY) as AppUserRole | null) ?? null;
    } catch {
      return null;
    }
  });

  const setIdentity = (nextUserId: string | null, nextRole: AppUserRole | null) => {
    // Synchronous, not effect-driven: an identity switch (e.g. via Login)
    // must be visible to the API client before any subsequently-mounted
    // component can fire a request.
    setApiAccessToken(null);
    setApiIdentity(nextUserId);
    setAccessTokenState(null);
    setUserIdState(nextUserId);
    setRoleLabelState(nextRole);
    try {
      window.localStorage.removeItem(ACCESS_TOKEN_KEY);
      if (nextUserId) {
        window.localStorage.setItem(USER_ID_KEY, nextUserId);
      } else {
        window.localStorage.removeItem(USER_ID_KEY);
      }
      if (nextRole) {
        window.localStorage.setItem(ROLE_KEY, nextRole);
      } else {
        window.localStorage.removeItem(ROLE_KEY);
      }
    } catch {
      // localStorage unavailable (e.g. private browsing) — identity still
      // works for the current session via in-memory state.
    }
  };

  const setSession = (nextToken: string, nextUserId: string, nextRole: AppUserRole) => {
    setApiIdentity(null);
    setApiAccessToken(nextToken);
    setUserIdState(nextUserId);
    setRoleLabelState(nextRole);
    setAccessTokenState(nextToken);
    try {
      window.localStorage.removeItem(USER_ID_KEY);
      window.localStorage.setItem(ACCESS_TOKEN_KEY, nextToken);
      window.localStorage.setItem(ROLE_KEY, nextRole);
    } catch {
      // localStorage unavailable (e.g. private browsing) — session still
      // works for the current page load via in-memory state.
    }
  };

  const value = useMemo(
    () => ({ userId, roleLabel, accessToken, setIdentity, setSession }),
    [userId, roleLabel, accessToken]
  );

  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>;
}

export function useIdentity(): IdentityContextValue {
  const ctx = useContext(IdentityContext);
  if (!ctx) {
    throw new Error("useIdentity must be used within an IdentityProvider");
  }
  return ctx;
}
