import { Outlet, Navigate } from "react-router-dom";
import { Component, type ReactNode, type ErrorInfo } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { useAuth } from "../features/auth/AuthProvider";
import { AlertTriangle } from "lucide-react";

// ── Auth loading skeleton ─────────────────────────────────────────────────────

function AuthLoading() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-atlas-50">
      <div className="text-center">
        <div className="w-8 h-8 rounded bg-atlas-900 flex items-center justify-center mx-auto mb-3">
          <span className="text-sm font-bold text-white">A</span>
        </div>
        <div className="animate-pulse">
          <div className="h-2 bg-atlas-200 rounded w-24 mx-auto" />
        </div>
      </div>
    </div>
  );
}

// ── Shell error boundary ──────────────────────────────────────────────────────

interface ErrorBoundaryState {
  hasError: boolean;
  message: string;
}

class ShellErrorBoundary extends Component<
  { children: ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, message: "" };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, message: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("AppLayout error boundary caught:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-atlas-50 p-8">
          <div className="max-w-md text-center">
            <AlertTriangle className="w-8 h-8 text-destructive-400 mx-auto mb-3" />
            <p className="text-sm font-medium text-atlas-800 mb-1">
              Something went wrong
            </p>
            <p className="text-xs text-atlas-500 mb-4">
              {this.state.message || "An unexpected error occurred."}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-atlas-900 text-white text-xs font-medium rounded hover:bg-atlas-800 transition-colors"
            >
              Reload page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── App layout ────────────────────────────────────────────────────────────────

export function AppLayout({ title }: { title?: string }) {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) return <AuthLoading />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;

  return (
    <ShellErrorBoundary>
      <div className="min-h-screen bg-atlas-50">
        <Sidebar />
        <TopBar title={title} />
        <main className="ml-[var(--sidebar-width)] pt-[var(--topbar-height)] min-h-screen">
          <Outlet />
        </main>
      </div>
    </ShellErrorBoundary>
  );
}
