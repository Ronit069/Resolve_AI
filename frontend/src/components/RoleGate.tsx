import type { ReactNode } from "react";
import type { AppUserRole } from "../api/types";
import { useIdentity } from "../state/IdentityContext";

interface RoleGateProps {
  allow: AppUserRole[];
  children: ReactNode;
  fallback?: ReactNode;
}

/**
 * UX-only role gate. Hides/disables UI for roles that would be rejected by
 * the server, purely to avoid presenting an action as free of consequence.
 *
 * This is NOT a security boundary. The current role label comes from the
 * client-side development identity selector (Login.tsx / IdentityContext),
 * which is never verified or sent to the server as an authorization claim.
 * Every actual gated action is re-validated server-side via require_role()
 * regardless of what this component renders.
 */
export function RoleGate({ allow, children, fallback = null }: RoleGateProps) {
  const { roleLabel } = useIdentity();
  if (roleLabel && allow.includes(roleLabel)) {
    return <>{children}</>;
  }
  return <>{fallback}</>;
}
