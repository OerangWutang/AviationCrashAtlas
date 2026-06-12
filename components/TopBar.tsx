import { Search, LogOut, User, AlertTriangle } from "lucide-react";
import { useState } from "react";
import { useAuth } from "../features/auth/AuthProvider";
import { useNavigate } from "react-router-dom";

const roleLabel: Record<string, string> = {
  analyst: "Analyst",
  reviewer: "Reviewer",
  admin: "Admin",
};

export function TopBar({ title }: { title?: string }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [logoutError, setLogoutError] = useState(false);

  const handleLogout = async () => {
    setUserMenuOpen(false);
    setLogoutError(false);
    try {
      await logout();
      navigate("/login", { replace: true });
    } catch {
      // Logout failed — still navigate away; AuthProvider already cleared state
      navigate("/login", { replace: true });
    }
  };

  return (
    <header className="fixed top-0 left-[var(--sidebar-width)] right-0 h-[var(--topbar-height)] bg-white border-b border-atlas-100 flex items-center px-4 gap-4 z-20">
      {/* Title / breadcrumb */}
      {title && (
        <h1 className="text-sm font-semibold text-atlas-900 truncate">
          {title}
        </h1>
      )}

      {/* Search — disabled until NL search is wired */}
      <div className="flex-1 max-w-md">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-atlas-400" />
          <input
            type="text"
            placeholder="Search claims, conflicts, sources…"
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-atlas-50 border border-atlas-200 rounded text-atlas-700 placeholder:text-atlas-400 focus:outline-none disabled:cursor-not-allowed"
            disabled
            title="Search coming soon"
          />
        </div>
      </div>

      <div className="flex-1" />

      {/* Logout error */}
      {logoutError && (
        <div className="flex items-center gap-1 text-2xs text-destructive-600">
          <AlertTriangle className="w-3 h-3" />
          Logout failed
        </div>
      )}

      {/* User menu */}
      {user && (
        <div className="relative">
          <button
            onClick={() => setUserMenuOpen(!userMenuOpen)}
            className="flex items-center gap-2 text-xs text-atlas-600 hover:text-atlas-900 transition-colors focus-ring rounded px-2 py-1"
            aria-expanded={userMenuOpen}
            aria-haspopup="true"
          >
            <div className="w-5 h-5 rounded-full bg-atlas-200 flex items-center justify-center">
              <User className="w-3 h-3 text-atlas-600" />
            </div>
            <span className="font-medium hidden sm:block">
              {user.displayName || user.email}
            </span>
            <span className="chip bg-atlas-100 text-atlas-600 border border-atlas-200 text-2xs">
              {roleLabel[user.role] ?? user.role}
            </span>
          </button>

          {userMenuOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setUserMenuOpen(false)}
              />
              <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-atlas-200 rounded shadow-lg py-1 min-w-[12rem]">
                <div className="px-3 py-2 border-b border-atlas-100">
                  <p className="text-xs font-medium text-atlas-800 truncate">
                    {user.email}
                  </p>
                  <p className="text-2xs text-atlas-500 mt-0.5">
                    {roleLabel[user.role] ?? user.role}
                    {user.tenantId ? " · Tenant" : " · System"}
                  </p>
                </div>
                <button
                  onClick={handleLogout}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-atlas-600 hover:bg-atlas-50 hover:text-atlas-900 transition-colors"
                >
                  <LogOut className="w-3.5 h-3.5" />
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </header>
  );
}
