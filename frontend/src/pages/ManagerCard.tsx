import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { ManagerCard as Manager } from "../company";
import { addressLines } from "../company";

// Who manages an owner's or a tenant's home (ADR 0006, step 12): the company's logo, name and
// mailing address, where a notice to the manager goes.
export function ManagerCard() {
  const [manager, setManager] = useState<Manager | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Manager>("/api/company/card").then(setManager)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);
  return (
    <Card title="Your property manager">
      {manager === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <div className="flex items-start gap-4">
          {manager.logo && <img src="/api/company/logo" alt={`${manager.name} logo`} className="h-12 w-auto max-w-40 object-contain" />}
          <address className="text-base not-italic">
            <span className="block font-medium">{manager.name}</span>
            {manager.address && addressLines(manager.address).map((line) => (
              <span key={line} className="block text-ink-muted">{line}</span>
            ))}
          </address>
        </div>
      )}
    </Card>
  );
}
