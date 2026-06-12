/**
 * RoleGate — renders children only for users with the given permission or role.
 *
 * Use `permission` for granular feature flags (from BFF /auth/me permissions map).
 * Use `role` for coarse role checks when a permission flag isn't specific enough.
 *
 * When the user lacks access, renders the `fallback` (default: nothing).
 * For write actions that should be visible but disabled, use `disabled` mode:
 *   <RoleGate permission="can_resolve_conflicts" disabled>
 *     <button>Resolve</button>
 *   </RoleGate>
 * In disabled mode the children render but are wrapped in a div with pointer-events-none
 * and a tooltip explaining why the action is gated.
 */

import { type ReactNode } from "react";
import { useAuth } from "../features/auth/AuthProvider";
import type { Role } from "../types/api";

interface RoleGateProps {
  children: ReactNode;
  /** Permission flag from the BFF permissions map (preferred). */
  permission?: string;
  /** Role check — user must have one of these roles. */
  role?: Role | Role[];
  /** Fallback to render when access is denied in hide mode. Default: null. */
  fallback?: ReactNode;
  /**
   * If true, render children in a disabled wrapper instead of hiding them.
   * Useful for buttons that should be visible but non-interactive.
   */
  disabled?: boolean;
  /** Tooltip shown in disabled mode. */
  disabledReason?: string;
}

export function RoleGate({
  children,
  permission,
  role,
  fallback = null,
  disabled = false,
  disabledReason = "You don't have permission to perform this action.",
}: RoleGateProps) {
  const { can, hasRole } = useAuth();

  const hasAccess =
    (permission ? can(permission) : true) &&
    (role
      ? Array.isArray(role)
        ? hasRole(...role)
        : hasRole(role)
      : true);

  if (hasAccess) return <>{children}</>;

  if (disabled) {
    return (
      <div
        title={disabledReason}
        className="pointer-events-none opacity-50 cursor-not-allowed select-none"
        aria-disabled="true"
      >
        {children}
      </div>
    );
  }

  return <>{fallback}</>;
}

/**
 * Hook version for imperative checks.
 * Returns { allowed, reason } so callers can gate non-rendered logic.
 */
export function usePermission(permission: string): boolean {
  const { can } = useAuth();
  return can(permission);
}
