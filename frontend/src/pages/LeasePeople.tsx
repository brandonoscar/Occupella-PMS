import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import type { RowAction } from "../components/RowMenu";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { day, money, usDate } from "../money";
import type { BreakFee, Household, LeaseDetail, LeasePerson } from "../rentals";
import { petLine, vehicleLine } from "../rentals";
import type { Recorder } from "./LeaseForms";
import { useRecord } from "./useRecord";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// One tenant leaving a lease others stay on (migration 0080): the day they left, and their
// deposit, which moves to a tenant who stays or stays under their name until move-out. It stays
// in trust either way; the PMS sends no money.
export function RemoveTenantForm({ lease, person, record }: { lease: LeaseDetail; person: LeasePerson; record: Recorder }) {
  const staying = lease.people.filter((p) => p.left_on === null && p.id !== person.id);
  const held = lease.tenants.find((t) => t.id === person.id)?.deposit ?? "0.00";
  const [leftOn, setLeftOn] = useState(day(new Date()));
  const [to, setTo] = useState("");
  const taker = staying.find((p) => p.id === to);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<{ moved: string }>(`/api/move-out/${lease.id}/departures`,
        { tenant_id: person.id, left_on: leftOn, deposit_to: to || null },
        (done) => Number(done.moved) > 0 && taker
          ? `${person.name} left the lease; their ${money(done.moved)} deposit moved to ${taker.name}.`
          : `${person.name} left the lease.`).then(() => undefined);
    }}>
      <p className="text-base text-ink">
        {person.name} leaves; the lease goes on with {staying.map((p) => p.name).join(", ")}.
      </p>
      <Field label="Left on" id={`left-${person.id}`} type="date" required value={leftOn}
        onChange={(e) => setLeftOn(e.target.value)} />
      {Number(held) > 0 && (
        <Select label={`Their ${money(held)} deposit`} id={`deposit-to-${person.id}`} value={to}
          onChange={(e) => setTo(e.target.value)}>
          <option value="">Stays under their name until move-out</option>
          {staying.map((p) => <option key={p.id} value={p.id}>Moves to {p.name}</option>)}
        </Select>
      )}
      <div>
        <Button type="submit" says={Number(held) <= 0
          ? "No money moves. They can still read the lease in the portal, but file nothing under it."
          : taker
            ? `Their ${money(held)} deposit moves to ${taker.name}; nothing is paid out. They can still read the lease in the portal.`
            : `Their ${money(held)} deposit stays theirs until move-out; nothing is paid out. They can still read the lease in the portal.`}>
          Take {person.name} off the lease
        </Button>
      </div>
    </Form>
  );
}

// Ending a lease from its page, any lease, with or without a deposit: its last day, and the
// lease-break fee its property's terms charge when a lease with an end date ends before it, only
// if staff tick it.
export function EndLeaseForm({ lease, record }: { lease: LeaseDetail; record: Recorder }) {
  const [last, setLast] = useState(lease.ends_on ?? day(new Date()));
  const [fee, setFee] = useState<BreakFee | null>(null);
  const [charge, setCharge] = useState(false);
  useEffect(() => {
    let live = true;
    if (last) {
      api.get<BreakFee>(`/api/move-out/${lease.id}/break-fee?ends_on=${last}`)
        .then((found) => { if (live) setFee(found); })
        .catch(() => { if (live) setFee(null); });
    }
    return () => { live = false; };
  }, [lease.id, last]);
  const offered = fee !== null && fee.early && fee.fee !== null ? fee.fee : null;
  const charging = offered !== null && charge;
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/move-out/${lease.id}/end`, { ends_on: last, break_fee: charging },
        () => charging
          ? `The lease ends on ${usDate(last)}; a ${money(offered)} lease-break fee was charged.`
          : `The lease ends on ${usDate(last)}.`).then(() => undefined);
    }}>
      <Field label="Last day" id={`end-${lease.id}`} type="date" required value={last}
        onChange={(e) => setLast(e.target.value)} />
      {offered !== null && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={charge} onChange={(e) => setCharge(e.target.checked)} />
          Charge the lease-break fee: {money(offered)}
        </label>
      )}
      {fee !== null && fee.early && fee.fee === null && (
        <p className="text-sm text-ink-muted">This lease's property charges no lease-break fee.</p>
      )}
      <div>
        <Button type="submit"
          says={`The lease ends on ${usDate(last)}; rent is charged only through that day${charging ? `, and the ${money(offered)} lease-break fee is charged` : ""}. The deposit is returned from Move-outs.`}>
          End lease
        </Button>
      </div>
    </Form>
  );
}

type Kind = "guarantors" | "occupants" | "pets" | "vehicles";
const ADD: Record<Kind, string> = {
  guarantors: "Add guarantor",
  occupants: "Add occupant",
  pets: "Add pet",
  vehicles: "Add vehicle",
};

// Who else lives there, their pets and vehicles, and who guarantees the lease, each added in a
// side panel and taken off from its ⋯ menu (a removal is a row: nothing is deleted).
export function HouseholdCards({ leaseId }: { leaseId: string }) {
  const [household, setHousehold] = useState<Household | null>(null);
  const [failed, setFailed] = useState("");
  const [adding, setAdding] = useState<Kind | null>(null);
  function load(keep: Keep = keepAll) {
    api.get<Household>(`/api/leases/${leaseId}/household`).then(keep(setHousehold))
      .catch(keep((error) => setFailed(detail(error))));
  }
  useEffect(() => {
    const run = latest();
    load(run.keep);
    return run.stop;
  }, [leaseId]);
  const { said, error, record } = useRecord(load);
  const addThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setAdding(null);
    return recorded;
  };

  if (household === null) {
    return <Notice tone={failed ? "error" : "info"}>{failed || "Loading"}</Notice>;
  }
  const off = (kind: Kind, id: string, what: string): RowAction[] => [{
    label: "Take off the lease",
    danger: true,
    confirm: `Take ${what} off this lease? Nothing is charged, refunded or sent. To put them back, add them again.`,
    onSelect: () => record(`/api/leases/${leaseId}/${kind}/${id}/remove`, {}, () => `${what} is off the lease.`),
  }];
  const section = (kind: Kind, title: string, rows: { id: string; what: string; cells: React.ReactNode[] }[]) => (
    <Card title={title}>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">None on file.</p>
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label={title}>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} aria-label={row.what}>
                  {row.cells.map((cell, n) => <td key={n}>{cell}</td>)}
                  <td className="text-right"><RowMenu label={row.what} actions={off(kind, row.id, row.what)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      <div className="mt-2"><Button size="sm" variant="secondary" onClick={() => setAdding(kind)}>{ADD[kind]}</Button></div>
    </Card>
  );
  return (
    <div className="flex flex-col gap-6">
      {section("guarantors", "Guarantors", household.guarantors.map((g) => ({
        id: g.id,
        what: g.name,
        cells: [g.name, g.email ?? "", g.phone ?? "", g.signed ? <Badge key="signed" tone="positive">Signed the lease</Badge> : ""],
      })))}
      {section("occupants", "Other occupants", household.occupants.map((o) => ({
        id: o.id,
        what: o.name,
        cells: [o.name, o.relationship ?? ""],
      })))}
      {section("pets", "Pets and animals", household.pets.map((p) => ({
        id: p.id,
        what: p.name,
        cells: [petLine(p), p.assistance_animal ? <Badge key="assistance" tone="info">Assistance animal</Badge> : ""],
      })))}
      {household.pets.some((p) => p.assistance_animal) && (
        <p className="text-sm text-ink-muted">
          An assistance animal is not a pet: no pet rent, fee or deposit is charged for it (Fair Housing Act).
        </p>
      )}
      {section("vehicles", "Vehicles", household.vehicles.map((v) => ({
        id: v.id,
        what: vehicleLine(v),
        cells: [vehicleLine(v)],
      })))}
      <Said>{said}</Said>
      {adding === null && <Notice tone="error">{error}</Notice>}
      {adding !== null && (
        <SidePanel title={ADD[adding]} onClose={() => setAdding(null)}>
          {adding === "guarantors" && <GuarantorForm leaseId={leaseId} record={addThenClose} />}
          {adding === "occupants" && <OccupantForm leaseId={leaseId} record={addThenClose} />}
          {adding === "pets" && <PetForm leaseId={leaseId} record={addThenClose} />}
          {adding === "vehicles" && <VehicleForm leaseId={leaseId} record={addThenClose} />}
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
    </div>
  );
}

// The form's fields left empty are sent as nothing.
function given(value: string): string | null {
  return value.trim() === "" ? null : value.trim();
}

function GuarantorForm({ leaseId, record }: { leaseId: string; record: Recorder }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/leases/${leaseId}/guarantors`, { name, email: given(email), phone: given(phone) },
        () => `${name} guarantees the lease.`).then(() => undefined);
    }}>
      <Field label="Name" id="guarantor-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="Email" id="guarantor-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Phone" id="guarantor-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
      <div>
        <Button type="submit" says="Adds them as the lease's guarantor. Nothing is sent to them, and they get no portal login.">
          Add guarantor
        </Button>
      </div>
    </Form>
  );
}

function OccupantForm({ leaseId, record }: { leaseId: string; record: Recorder }) {
  const [name, setName] = useState("");
  const [relationship, setRelationship] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/leases/${leaseId}/occupants`, { name, relationship: given(relationship) },
        () => `${name} lives there too.`).then(() => undefined);
    }}>
      <Field label="Name" id="occupant-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="Relationship to the tenants" id="occupant-relationship" maxLength={100} value={relationship}
        onChange={(e) => setRelationship(e.target.value)} />
      <div>
        <Button type="submit" says="Adds them as living there, not as a tenant on the lease. Nothing is charged or sent.">
          Add occupant
        </Button>
      </div>
    </Form>
  );
}

function PetForm({ leaseId, record }: { leaseId: string; record: Recorder }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("");
  const [breed, setBreed] = useState("");
  const [weight, setWeight] = useState("");
  const [assistance, setAssistance] = useState(false);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/leases/${leaseId}/pets`,
        { name, kind, breed: given(breed), weight_lbs: given(weight), assistance_animal: assistance },
        () => `${name} is on the lease.`).then(() => undefined);
    }}>
      <Field label="Name" id="pet-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="Kind (dog, cat...)" id="pet-kind" required maxLength={50} value={kind} onChange={(e) => setKind(e.target.value)} />
      <Field label="Breed" id="pet-breed" maxLength={100} value={breed} onChange={(e) => setBreed(e.target.value)} />
      <Field label="Weight (lb)" id="pet-weight" inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} />
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={assistance} onChange={(e) => setAssistance(e.target.checked)} />
        An assistance animal (service or support): no pet rent, fee or deposit is charged for it
      </label>
      <div>
        <Button type="submit" says={assistance
          ? "Adds the assistance animal to the lease. No pet rent, fee or deposit is charged for it."
          : "Adds the pet to the lease. Nothing is charged: add pet rent or a pet deposit yourself."}>
          Add pet
        </Button>
      </div>
    </Form>
  );
}

function VehicleForm({ leaseId, record }: { leaseId: string; record: Recorder }) {
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [color, setColor] = useState("");
  const [plate, setPlate] = useState("");
  const [state, setState] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/leases/${leaseId}/vehicles`,
        { make, model: given(model), color: given(color), plate, plate_state: given(state) },
        () => `The ${make} is on the lease.`).then(() => undefined);
    }}>
      <Field label="Make" id="vehicle-make" required maxLength={50} value={make} onChange={(e) => setMake(e.target.value)} />
      <Field label="Model" id="vehicle-model" maxLength={50} value={model} onChange={(e) => setModel(e.target.value)} />
      <Field label="Color" id="vehicle-color" maxLength={30} value={color} onChange={(e) => setColor(e.target.value)} />
      <Field label="Plate" id="vehicle-plate" required maxLength={15} value={plate} onChange={(e) => setPlate(e.target.value)} />
      <Field label="Plate state" id="vehicle-state" maxLength={30} value={state} onChange={(e) => setState(e.target.value)} />
      <div>
        <Button type="submit" says="Adds the vehicle to the lease. Nothing is charged or sent.">Add vehicle</Button>
      </div>
    </Form>
  );
}
