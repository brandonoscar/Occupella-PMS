import type { Me } from "../api";
import { api } from "../api";
import { navigate } from "../router";
import { Button } from "./Button";

export function Wordmark() {
  return <span className="font-display text-lg font-medium lowercase text-ink">occupella</span>;
}

// The frame around a signed-in page: who and which PMC, and a way to sign out.
export function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  async function signOut() {
    await api.post("/api/sign-out");
    navigate(`/sign-in?pmc=${me.pmc_id}`);
  }
  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-line px-6 py-3">
        <div className="flex items-baseline gap-3">
          <Wordmark />
          <span className="text-sm text-ink-subtle">{me.pmc_name}</span>
        </div>
        <div className="flex items-center gap-4 text-sm text-ink-muted">
          <span>{me.name}</span>
          <Button variant="ghost" onClick={signOut}>
            Sign out
          </Button>
        </div>
      </header>
      <main className="mx-auto flex max-w-5xl flex-col gap-8 px-6 py-8">{children}</main>
    </div>
  );
}

export function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-card-edge bg-raised">
      <h2 className="border-b border-line px-5 py-3 text-md font-medium">{title}</h2>
      <div className="p-5">{children}</div>
    </section>
  );
}
