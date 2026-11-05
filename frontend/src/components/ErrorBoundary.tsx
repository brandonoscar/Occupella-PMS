import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "./Button";

// When a page fails to draw (an answer of a shape it doesn't know after an upgrade, say), React
// unmounts everything: the portal went blank white (docs/edge-cases.md H-152). This keeps a page
// saying so and what to do, never the error's own words; the error goes to the console.
export class ErrorBoundary extends Component<
  { children: ReactNode; reload?: () => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("the page failed to draw", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    const reload = this.props.reload ?? (() => window.location.reload());
    return (
      <div role="alert" className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-6">
        <h1 className="font-display text-lg font-semibold text-ink">This page couldn&apos;t be shown.</h1>
        <p className="text-base text-ink-muted">
          Nothing you saved is lost. Reload the page; if it happens again, go to the start, and tell
          whoever runs the PMS which page it was.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={reload}>Reload</Button>
          <a href="/" className="text-sm text-iris hover:text-iris-hover hover:underline">Go to the start</a>
        </div>
      </div>
    );
  }
}
