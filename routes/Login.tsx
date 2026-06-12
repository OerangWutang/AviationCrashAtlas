import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../features/auth/AuthProvider";
import { isApiError } from "../lib/api";
import { AlertTriangle } from "lucide-react";

export default function Login() {
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // If already logged in, redirect
  if (isAuthenticated) {
    navigate("/app", { replace: true });
    return null;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login({ email, password });
      navigate("/app");
    } catch (err) {
      if (isApiError(err) && err.status === 401) {
        setError("Invalid email or password.");
      } else {
        setError("Unable to connect. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-atlas-950 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-10 h-10 rounded bg-white/10 flex items-center justify-center mx-auto mb-4">
            <span className="text-lg font-bold text-white tracking-tight">
              A
            </span>
          </div>
          <h1 className="text-lg font-semibold text-white">Atlas</h1>
          <p className="text-xs text-atlas-400 mt-1">
            The record you can defend.
          </p>
        </div>

        {/* Form */}
        <form
          onSubmit={handleSubmit}
          className="bg-white rounded-lg shadow-xl p-6 space-y-4"
        >
          <div>
            <label
              htmlFor="email"
              className="block text-xs font-medium text-atlas-700 mb-1"
            >
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-atlas-200 rounded focus:outline-none focus:ring-2 focus:ring-atlas-400 focus:border-atlas-400 text-atlas-900 placeholder:text-atlas-400"
              placeholder="you@firm.com"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-xs font-medium text-atlas-700 mb-1"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-atlas-200 rounded focus:outline-none focus:ring-2 focus:ring-atlas-400 focus:border-atlas-400 text-atlas-900 placeholder:text-atlas-400"
              placeholder="••••••••"
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 text-sm text-destructive-600 bg-destructive-50 border border-destructive-200 rounded px-3 py-2">
              <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full py-2 px-4 bg-atlas-900 text-white text-sm font-medium rounded hover:bg-atlas-800 transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-ring"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="text-center text-2xs text-atlas-500 mt-6">
          Atlas is an evidence-organization tool, not a provider of legal
          advice.
        </p>
      </div>
    </div>
  );
}
