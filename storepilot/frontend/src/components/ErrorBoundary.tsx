import { Component, type ReactNode } from "react";

/** A bug in one page must not blank the whole screen: STOP and the tabs stay usable. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-4 rounded-xl border border-err/50 bg-err/10 p-5 text-[15px]">
        <p className="font-bold text-[#ffb4b2]">This page hit an error and was stopped.</p>
        <p className="mt-1 text-muted">The STOP button and the other tabs still work.</p>
        <p className="num mt-3 break-words text-[13px] text-dim">{String(this.state.error.message || this.state.error)}</p>
        <button onClick={() => location.reload()}
          className="mt-4 min-h-12 rounded-lg border border-line-strong bg-raised px-4 font-semibold text-ink active:bg-line">
          Reload dashboard
        </button>
      </div>
    );
  }
}
