import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { agreementSummary } from "../setup";
import type { CardProps } from "./useAdd";
import { useAdd } from "./useAdd";

const BLANK = {
  account: "",
  starts_on: "",
  fee_percent: "",
  minimum_fee: "",
  flat_fee: "",
  leasing_fee_percent: "",
  reserve: "",
  approval_limit: "",
};
const AMOUNTS = [
  ["fee_percent", "Fee, percent of rent collected"],
  ["minimum_fee", "Minimum fee"],
  ["flat_fee", "Flat fee"],
  ["leasing_fee_percent", "Leasing fee, percent of rent"],
  ["reserve", "Reserve"],
  ["approval_limit", "Owner approves bills over"],
] as const;

// Management terms per owner's property account, in force from a day. New terms are new
// terms from a later day; earlier ones stay as they were.
export function TermsCard({ books, refresh }: CardProps) {
  const { error, add } = useAdd(refresh);
  const [terms, setTerms] = useState(BLANK);
  return (
    <Card title="Management terms">
      <ul className="mb-6 flex flex-col gap-2">
        {books.accounts.map((a) => (
          <li key={a.account}>
            {a.owner}, {a.property}:{" "}
            <span className="text-sm text-ink-muted">
              {a.agreement
                ? `${agreementSummary(a.agreement)} (from ${a.agreement.starts_on})`
                : "no terms in force"}
            </span>
          </li>
        ))}
      </ul>
      <form
        className="flex flex-col gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const amounts = Object.fromEntries(AMOUNTS.map(([key]) => [key, terms[key] || "0"]));
          if (await add("agreements", { ...terms, ...amounts })) setTerms(BLANK);
        }}
      >
        <div className="flex flex-wrap items-end gap-3">
          <Select label="For" id="terms-account" required value={terms.account}
            onChange={(e) => setTerms({ ...terms, account: e.target.value })}>
            <option value="">Choose an owner's property</option>
            {books.accounts.map((a) => (
              <option key={a.account} value={a.account}>
                {a.owner}, {a.property}
              </option>
            ))}
          </Select>
          <Field label="In force from" id="terms-from" type="date" required value={terms.starts_on}
            onChange={(e) => setTerms({ ...terms, starts_on: e.target.value })} />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          {AMOUNTS.map(([key, label]) => (
            <Field key={key} label={label} id={`terms-${key}`} inputMode="decimal"
              value={terms[key]} onChange={(e) => setTerms({ ...terms, [key]: e.target.value })} />
          ))}
        </div>
        <div>
          <Button type="submit" variant="secondary">Set terms</Button>
        </div>
      </form>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
