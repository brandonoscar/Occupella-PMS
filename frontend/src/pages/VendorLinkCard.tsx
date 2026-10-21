import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/Confirm";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { latest } from "../latest";
import { usDate } from "../money";
import { useRecord } from "./useRecord";

// A vendor's private link (migration 0086): with it they see the work orders assigned to them,
// schedule a visit and say the work is done, without a login. The link is shown once, to copy and
// send them; a new one replaces the old, and Take back ends it.
export function VendorLinkCard({ vendorId, name }: { vendorId: string; name: string }) {
  const [madeAt, setMadeAt] = useState<string | null | undefined>(undefined);
  const [link, setLink] = useState("");
  const [asking, setAsking] = useState(false);
  const [runs, setRuns] = useState(0);
  const [unread, setUnread] = useState("");
  const { said, error, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    const run = latest();
    api
      .get<{ made_at: string | null }>(`/api/vendors/${vendorId}/link`)
      .then(run.keep((state) => setMadeAt(state.made_at)))
      .catch(run.keep((failed) => setUnread(failed instanceof ApiError ? failed.detail : String(failed))));
    return run.stop;
  }, [vendorId, runs]);
  if (madeAt === undefined) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  return (
    <Card title="Work order link">
      <p className="text-base text-ink-muted" data-testid="vendor-link">
        {madeAt
          ? `${name} has a link, made ${usDate(madeAt)}. With it they see their work orders, schedule a visit and say the work is done.`
          : `${name} has no link. Make one to send them: with it they see their work orders, schedule a visit and say the work is done.`}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="secondary" onClick={() => record<{ url: string }>(`/api/vendors/${vendorId}/link`, {}, (made) => {
          setLink(made.url);
          return madeAt ? "A new link replaces the old one; it is below, shown once." : "Link made; it is below, shown once.";
        })}>
          {madeAt ? "Make a new link" : "Make a link"}
        </Button>
        {madeAt && <Button variant="ghost" onClick={() => setAsking(true)}>Take back</Button>}
      </div>
      {link && <input readOnly aria-label={`Work order link, ${name}`} value={link}
        className="mt-3 w-full rounded-sm border border-line-strong bg-sunken px-2 py-1.5 font-mono text-sm break-all text-ink" />}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      {asking && (
        <ConfirmDialog
          question={`Take back ${name}'s link? It will open nothing from now on.`}
          confirmLabel="Take back"
          danger
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            setLink("");
            void record(`/api/vendors/${vendorId}/link/revoke`, {}, () => `${name}'s link is taken back.`);
          }}
        />
      )}
    </Card>
  );
}
