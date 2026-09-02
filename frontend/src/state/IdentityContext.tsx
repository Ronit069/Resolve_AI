import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setApiIdentity } from "../api/client";
import type { AppUserRole } from "../api/types";

const USER_ID_KEY = "resolveai.dev.userId";
const ROLE_KEY = "resolveai.dev.roleLabel";

interface IdentityContextValue {
  userId: string | null;
  /**
   * Purely a client-side display/UX-gating label the developer types in
   * alongside their user ID. The backend has no "who am I" endpoint to
   * fetch this from, and Module I is not authorized to add one. This
   * value is NEVER sent to the server and NEVER used as an authorization
   * claim — every gated backend call re-checks the real role itself via
   * require_role(). See RoleGate.tsx.
   */
  roleLabel: AppUserRole | null;
  setIdentity: (userId: string | null, roleLabel: AppUserRole | null) => void;
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
  const [userId, setUserIdState] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(USER_ID_KEY);
    } catch {
      return null;
    }
  });
  const [roleLabel, setRoleLabelState] = useState<AppUserRole | null>(() => {
    try {
      return (window.localStorage.getItem(ROLE_KEY) as AppUserRole | null) ?? null;
    } catch {
      return null;
    }
  });

  useEffect(() => {
    setApiIdentity(userId);
  }, [userId]);

  const setIdentity = (nextUserId: string | null, nextRole: AppUserRole | null) => {
    setUserIdState(nextUserId);
    setRoleLabelState(nextRole);
    try {
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

  const value = useMemo(() => ({ userId, roleLabel, setIdentity }), [userId, roleLabel]);

  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>;
}

export function useIdentity(): IdentityContextValue {
  const ctx = useContext(IdentityContext);
  if (!ctx) {
    throw new Error("useIdentity must be used within an IdentityProvider");
  }
  return ctx;
}
