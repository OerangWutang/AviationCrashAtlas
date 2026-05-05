/**
 * ErrorBoundary — catches uncaught React render errors in child trees.
 *
 * Use to wrap panels that depend on external data (provenance, conflicts)
 * so a single malformed API response doesn't blank the entire page.
 *
 * Usage:
 *   <ErrorBoundary label="Provenance">
 *     <ProvenancePanel ... />
 *   </ErrorBoundary>
 */
import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Short name shown in the fallback UI — e.g. "Provenance", "Conflicts" */
  label?: string;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    // Log to console in dev; a real app would send to Sentry / similar.
    console.error('[ErrorBoundary]', this.props.label ?? 'Panel', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="p-4 rounded-xl border border-red-200 bg-red-50">
          <div className="text-[11px] font-medium text-red-700 mb-1">
            {this.props.label ? `${this.props.label} failed to render` : 'This panel encountered an error'}
          </div>
          <div
            className="text-[10px] text-red-500 font-mono break-words"
            style={{ fontFamily: 'var(--ff-mono)' }}
          >
            {this.state.error.message}
          </div>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="mt-3 text-[10px] text-red-600 underline hover:no-underline"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
