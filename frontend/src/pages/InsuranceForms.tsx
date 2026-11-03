import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { day, usDate } from "../money";
import type { RentLease } from "../rent";
import { Form } from "../components/Form";

type Recorder = <T>(path: string, body: object, done: (answer: T) => string) => Promise<boolean>;

// Whether a lease requires renters insurance (and the least liability it asks), and a tenant's
// policy, with whatever insurer they chose: the PMS never picks one or charges for one.
export function InsuranceForms({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [required, setRequired] = useState(lease.renters_insurance?.required ?? false);
  const [minimum, setMinimum] = useState("");
  const [from, setFrom] = useState(day(new Date()));
  return (
    <div className="flex flex-col gap-4">
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = { required, minimum_liability: required && minimum ? minimum : null, starts_on: from };
          const said = () => (required ? "Renters insurance is required from " : "Renters insurance is not required from ") + `${usDate(from)}.`;
          if (await record(`/api/rent/leases/${lease.id}/renters-insurance-terms`, body, said)) setMinimum("");
        }}
      >
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
          Requires renters insurance
        </label>
        {required && (
          <Field label="Least liability coverage (optional)" id={`insurance-minimum-${lease.id}`} inputMode="decimal"
            value={minimum} onChange={(e) => setMinimum(e.target.value)} />
        )}
        <Field label="Asked from" id={`insurance-from-${lease.id}`} type="date" required value={from}
          onChange={(e) => setFrom(e.target.value)} />
        <Button type="submit" variant="secondary" says={required
          ? "The lease asks for renters insurance from that day; the tenant sees it in their portal. Nothing is charged."
          : "From that day the lease doesn't ask for renters insurance; earlier terms stay. Nothing is charged."}>
          Set insurance terms
        </Button>
      </Form>
      <PolicyForm path={`/api/rent/leases/${lease.id}/renters-insurance`} id={lease.id} record={record} />
    </div>
  );
}

// A renters insurance policy: the insurer the tenant chose, its number, the days it runs, and
// its liability coverage. Used by staff on the rent page and by the tenant in the portal.
export function PolicyForm({ path, id, record }: { path: string; id: string; record: Recorder }) {
  const [carrier, setCarrier] = useState("");
  const [number, setNumber] = useState("");
  const [starts, setStarts] = useState(day(new Date()));
  const [expires, setExpires] = useState("");
  const [limit, setLimit] = useState("");
  const [named, setNamed] = useState(false);
  return (
    <Form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = {
          carrier, policy_number: number, starts_on: starts, expires_on: expires,
          liability_limit: limit || null, landlord_interested_party: named,
        };
        if (await record(path, body, () => `Policy ${number} from ${carrier} recorded.`)) {
          setCarrier("");
          setNumber("");
          setLimit("");
        }
      }}
    >
      <Field label="Insurer" id={`policy-carrier-${id}`} required maxLength={200} value={carrier}
        onChange={(e) => setCarrier(e.target.value)} />
      <Field label="Policy number" id={`policy-number-${id}`} required maxLength={100} value={number}
        onChange={(e) => setNumber(e.target.value)} />
      <Field label="Policy starts" id={`policy-starts-${id}`} type="date" required value={starts}
        onChange={(e) => setStarts(e.target.value)} />
      <Field label="Policy runs out" id={`policy-expires-${id}`} type="date" required value={expires}
        onChange={(e) => setExpires(e.target.value)} />
      <Field label="Liability coverage (optional)" id={`policy-limit-${id}`} inputMode="decimal" value={limit}
        onChange={(e) => setLimit(e.target.value)} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={named} onChange={(e) => setNamed(e.target.checked)} />
        The landlord is named an interested party
      </label>
      <Button type="submit" variant="secondary"
        says="Adds this policy to the lease; earlier ones stay on file. Nothing is charged, and the insurer isn't contacted.">
        Record policy
      </Button>
    </Form>
  );
}
