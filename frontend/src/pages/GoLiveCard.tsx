import { Badge } from "../components/Badge";
import { Card } from "../components/Shell";
import { GoLink } from "./NeedsAttention";

// What a company still needs before it goes live (GET /api/go-live): each step worked out from
// its own records, shown on the Dashboard until every required one is done.
export type GoLiveStep = {
  key: string;
  label: string;
  detail: string;
  done: boolean;
  required: boolean;
  href: string;
};

export type GoLive = { steps: GoLiveStep[]; ready: boolean };

export function GoLiveCard({ goLive }: { goLive: GoLive }) {
  if (goLive.ready) return null;
  const left = goLive.steps.filter((step) => step.required && !step.done).length;
  return (
    <Card title="Getting started">
      <p className="mb-2 text-sm text-ink-muted">
        {left === 1 ? "1 step left before you go live." : `${left} steps left before you go live.`}
      </p>
      <ul aria-label="Getting started" className="divide-y divide-line rounded-md border border-card-edge">
        {goLive.steps.map((step) => (
          <li key={step.key} className="flex items-start justify-between gap-3 px-4 py-3">
            <div className="flex min-w-0 flex-col gap-0.5">
              <GoLink href={step.href} className="text-sm font-medium text-ink hover:underline">
                {step.label}
              </GoLink>
              <span className="text-sm text-ink-muted">
                {step.detail}
                {step.required ? "" : " (optional)"}
              </span>
            </div>
            <Badge tone={step.done ? "positive" : step.required ? "caution" : "neutral"}>
              {step.done ? "Done" : "To do"}
            </Badge>
          </li>
        ))}
      </ul>
    </Card>
  );
}
