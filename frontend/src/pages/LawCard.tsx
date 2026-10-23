import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import type { Facts, Jurisdiction, Law, Level, RuleKind, Shown } from "../law";
import { citedLine, confirmedLine, factLines, placeLine, placeSource } from "../law";
import { latest } from "../latest";
import { day, usDate } from "../money";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// The units each kind of rule takes (migration 20261023000202's check), with their words.
const UNITS: Record<RuleKind, [string, string][]> = {
  deposit_cap: [["months_of_rent", "months of rent"], ["dollars", "dollars"]],
  late_fee_cap: [["dollars", "dollars"], ["percent_of_rent", "% of the monthly rent"], ["percent_of_unpaid", "% of the rent unpaid"]],
  nsf_fee_cap: [["dollars", "dollars"]],
  rent_cap: [["percent_a_year", "% a year"]],
  deposit_return_days: [["days", "days"]],
  late_fee_grace_days: [["days", "days"]],
  rent_increase_notice_days: [["days", "days"]],
};

// A property's place in the law and the rules it keeps to (q142; ADR 0009): where its address
// is (found by the geocoder, or as staff confirm or correct it), the facts rules turn on, and each
// kind of rule in force today, cited with the day it was read and whether the company confirmed
// it, or why it is unknown (never "no rule"). Staff add a rule they read in a statute's text,
// correct one, and confirm the ones the company follows.
export function LawCard({ propertyId, again }: { propertyId: string; again: unknown }) {
  const [law, setLaw] = useState<Law | null>(null);
  const [loadError, setLoadError] = useState("");
  const [open, setOpen] = useState<"" | "place" | "facts" | "rule">("");
  const [correcting, setCorrecting] = useState<Shown | null>(null);
  const [tick, setTick] = useState(0);
  const { said, error, record } = useRecord(() => setTick((t) => t + 1));
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setOpen("");
    return recorded;
  };

  useEffect(() => {
    const run = latest();
    api.get<unknown>(`/api/properties/${propertyId}/law`)
      .then(run.keep((answer: unknown) => setLaw(isLaw(answer) ? answer : null)))
      .catch(run.keep((failed: unknown) => setLoadError(failed instanceof ApiError ? failed.detail : String(failed))));
    return run.stop;
  }, [propertyId, again, tick]);

  if (law === null) {
    return <Card title="State and local rules"><Notice tone="error">{loadError}</Notice></Card>;
  }
  const where = law.jurisdiction;
  return (
    <Card title="State and local rules">
      <div className="flex flex-col gap-4">
        <Said>{said}</Said>
        <section aria-label="Where it is in the law" className="flex flex-col gap-1 text-base">
          {where === null ? (
            <p className="text-ink-muted" data-testid="law-place">
              {law.has_address
                ? "Where its address is in the law isn't on file yet: find it on the map, or say where it is."
                : "Add the property's address first."}
            </p>
          ) : (
            <>
              <p data-testid="law-place">{placeLine(where)}{where.tract ? `, tract ${where.tract}` : ""}</p>
              <p className="text-sm text-ink-muted">{placeSource(where)}</p>
            </>
          )}
          {law.has_address && (
            <div className="mt-1 flex flex-wrap gap-2">
              {where !== null && where.source !== "staff" && (
                <Button size="sm" variant="secondary" onClick={() => record(
                  `/api/properties/${propertyId}/jurisdiction`, placeBody(where), () => "Where it is in the law confirmed.",
                )}>Confirm where it is</Button>
              )}
              <Button size="sm" variant="secondary" onClick={() => setOpen("place")}>
                {where === null ? "Say where it is" : "Correct where it is"}
              </Button>
            </div>
          )}
        </section>
        <section aria-label="Facts the rules turn on" className="flex flex-col gap-2">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-base sm:grid-cols-2">
            {factLines(law.facts).map(([what, value]) => (
              <div key={what} className="flex gap-2"><dt className="text-ink-muted">{what}:</dt><dd>{value}</dd></div>
            ))}
          </dl>
          <div><Button size="sm" variant="secondary" onClick={() => setOpen("facts")}>Edit facts</Button></div>
        </section>
        <Wide>
          <table className="w-full text-base" aria-label="Rules in force today">
            <thead>
              <tr>
                <th className="text-left">Rule</th>
                <th className="text-left">In force today</th>
                <th className="text-left">Source</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {law.rules.map((r) => (
                <tr key={r.rule} aria-label={r.label}>
                  <td>{r.label}</td>
                  <td className={r.known ? "" : "text-ink-muted"}>
                    {r.words}
                    {r.exceptions && <span className="block text-sm text-ink-muted">{`Exceptions: ${r.exceptions}`}</span>}
                  </td>
                  <td className="text-sm text-ink-muted">
                    {r.known ? (
                      <span className="flex flex-col items-start gap-1">
                        {r.url ? <a className={LINK} href={r.url} target="_blank" rel="noopener noreferrer">{citedLine(r)}</a> : citedLine(r)}
                        <Badge tone={r.confirmed_by ? "positive" : "caution"}>{confirmedLine(r)}</Badge>
                      </span>
                    ) : <Badge>Unknown</Badge>}
                  </td>
                  <td className="text-right">
                    <span className="inline-flex gap-2">
                      {r.known && r.confirmed_by === null && (
                        <Button size="sm" variant="secondary" onClick={() => record(
                          `/api/jurisdiction-rules/${r.rule_id}/confirm`, {}, () => `${r.label}: confirmed.`,
                        )}>Confirm</Button>
                      )}
                      {r.known && law.levels.some((lv) => lv.name === r.jurisdiction_name) && (
                        <Button size="sm" variant="secondary" onClick={() => { setCorrecting(r); setOpen("rule"); }}>Correct</Button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
        <div>
          <Button variant="secondary" onClick={() => { setCorrecting(null); setOpen("rule"); }}>Add a rule</Button>
        </div>
        <p className="text-xs text-ink-muted">{law.note}</p>
        {open === "" && <Notice tone="error">{error}</Notice>}
      </div>
      {open === "place" && (
        <SidePanel title={where === null ? "Say where it is" : "Correct where it is"} onClose={() => setOpen("")}>
          <PlaceForm propertyId={propertyId} where={where} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
      {open === "facts" && (
        <SidePanel title="Edit facts" onClose={() => setOpen("")}>
          <FactsForm propertyId={propertyId} facts={law.facts} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
      {open === "rule" && (
        <SidePanel title={correcting ? `Correct: ${correcting.label}` : "Add a rule"} onClose={() => setOpen("")}>
          <RuleForm propertyId={propertyId} levels={law.levels} correcting={correcting} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

function isLaw(answer: unknown): answer is Law {
  return typeof answer === "object" && answer !== null && Array.isArray((answer as Law).rules)
    && Array.isArray((answer as Law).levels);
}

function placeBody(where: Jurisdiction) {
  return {
    county_fips: where.county_fips, county_name: where.county_name,
    cousub_fips: where.cousub_fips, cousub_name: where.cousub_name,
    place_fips: where.place_fips, place_name: where.place_name, tract: where.tract,
  };
}

const orNull = (value: string) => (value.trim() === "" ? null : value.trim());

function PlaceForm({ propertyId, where, record }: { propertyId: string; where: Jurisdiction | null; record: Recorder }) {
  const [place, setPlace] = useState({
    county_fips: where?.county_fips ?? "", county_name: where?.county_name ?? "",
    cousub_fips: where?.cousub_fips ?? "", cousub_name: where?.cousub_name ?? "",
    place_fips: where?.place_fips ?? "", place_name: where?.place_name ?? "", tract: where?.tract ?? "",
  });
  const set = (key: keyof typeof place) => (e: React.ChangeEvent<HTMLInputElement>) => setPlace({ ...place, [key]: e.target.value });
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = Object.fromEntries(Object.entries(place).map(([k, v]) => [k, orNull(v)]));
      return record(`/api/properties/${propertyId}/jurisdiction`, body, () => "Where it is in the law saved.");
    }}>
      <p className="text-sm text-ink-muted">
        The Census Bureau&apos;s codes and names for where the address is: the state is the address&apos;s. Leave the
        city empty for an address outside any city, town or village.
      </p>
      <Field label="County code (three digits)" id="place-county-fips" maxLength={3} inputMode="numeric" value={place.county_fips} onChange={set("county_fips")} />
      <Field label="County" id="place-county-name" maxLength={100} value={place.county_name} onChange={set("county_name")} />
      <Field label="County subdivision code (five digits)" id="place-cousub-fips" maxLength={5} inputMode="numeric" value={place.cousub_fips} onChange={set("cousub_fips")} />
      <Field label="County subdivision" id="place-cousub-name" maxLength={100} value={place.cousub_name} onChange={set("cousub_name")} />
      <Field label="City code (five digits)" id="place-city-fips" maxLength={5} inputMode="numeric" value={place.place_fips} onChange={set("place_fips")} />
      <Field label="City" id="place-city-name" maxLength={100} value={place.place_name} onChange={set("place_name")} />
      <Field label="Census tract (six digits)" id="place-tract" maxLength={6} inputMode="numeric" value={place.tract} onChange={set("tract")} />
      <div><Button type="submit">Save where it is</Button></div>
    </Form>
  );
}

const yesNo = (value: boolean | null) => (value === null ? "" : value ? "yes" : "no");

function FactsForm({ propertyId, facts, record }: { propertyId: string; facts: Facts; record: Recorder }) {
  const [year, setYear] = useState(facts.year_built === null ? "" : String(facts.year_built));
  const [units, setUnits] = useState(facts.units_in_building === null ? "" : String(facts.units_in_building));
  const [occupied, setOccupied] = useState(yesNo(facts.owner_occupied));
  const [kind, setKind] = useState(facts.owner_kind ?? "");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        year_built: year.trim() ? Number(year) : null,
        units_in_building: units.trim() ? Number(units) : null,
        owner_occupied: occupied === "" ? null : occupied === "yes",
        owner_kind: kind === "" ? null : kind,
      };
      return record(`/api/properties/${propertyId}/facts`, body, () => "Facts saved.");
    }}>
      <p className="text-sm text-ink-muted">What some rules turn on. Leave one empty while it isn&apos;t known: a rule that turns on it stays unknown.</p>
      <Field label="Year built" id="facts-year" inputMode="numeric" maxLength={4} value={year} onChange={(e) => setYear(e.target.value)} />
      <Field label="Units in the building" id="facts-units" inputMode="numeric" value={units} onChange={(e) => setUnits(e.target.value)} />
      <Select label="An owner lives there" id="facts-occupied" value={occupied} onChange={(e) => setOccupied(e.target.value)}>
        <option value="">Not known</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </Select>
      <Select label="The owner is" id="facts-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="">Not known</option>
        <option value="person">A person</option>
        <option value="company">A company</option>
      </Select>
      <div><Button type="submit">Save facts</Button></div>
    </Form>
  );
}

const KIND_WORDS: [RuleKind, string][] = [
  ["deposit_cap", "Security deposit: the most"],
  ["deposit_return_days", "Deposit returned within"],
  ["late_fee_grace_days", "Days before a late fee"],
  ["late_fee_cap", "Late fee: the most"],
  ["nsf_fee_cap", "Returned payment (NSF) fee: the most"],
  ["rent_increase_notice_days", "Notice of a rent increase"],
  ["rent_cap", "Rent increase in a year: the most"],
];

function RuleForm({ propertyId, levels, correcting, record }: {
  propertyId: string; levels: Level[]; correcting: Shown | null; record: Recorder;
}) {
  const fixedLevel = correcting ? levels.find((lv) => lv.name === correcting.jurisdiction_name)?.level : undefined;
  const [level, setLevel] = useState<Level["level"]>(fixedLevel ?? levels.find((lv) => lv.level === "state")?.level ?? "federal");
  const [kind, setKind] = useState<RuleKind>(correcting?.rule ?? "deposit_cap");
  const [valueKind, setValueKind] = useState<"number" | "none" | "words">("number");
  const [number, setNumber] = useState("");
  const [unit, setUnit] = useState(UNITS[kind][0]![0]);
  const [combine, setCombine] = useState("");
  const [also, setAlso] = useState("");
  const [alsoUnit, setAlsoUnit] = useState(UNITS[kind][0]![0]);
  const [words, setWords] = useState("");
  const [from, setFrom] = useState(day(new Date()));
  const [until, setUntil] = useState("");
  const [citation, setCitation] = useState("");
  const [url, setUrl] = useState("https://");
  const [checkedOn, setCheckedOn] = useState(day(new Date()));
  const [reviewer, setReviewer] = useState("");
  const [exceptions, setExceptions] = useState("");
  const [cond, setCond] = useState({ built_before: "", built_on_or_after: "", units_at_least: "", units_at_most: "", owner_occupied: "", owner_kind: "", precedence: "0" });
  const setC = (key: keyof typeof cond) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setCond({ ...cond, [key]: e.target.value });
  const units = UNITS[kind];
  const pickKind = (next: RuleKind) => {
    setKind(next);
    setUnit(UNITS[next][0]![0]);
    setAlsoUnit(UNITS[next][0]![0]);
    setCombine("");
  };
  const whole = (v: string) => (v.trim() ? Number(v) : null);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        property_id: propertyId, level, rule: kind, value_kind: valueKind,
        value_number: valueKind === "number" ? number.trim() : null,
        value_unit: valueKind === "number" ? unit : null,
        combine: valueKind === "number" && combine ? combine : null,
        also_number: valueKind === "number" && combine ? also.trim() : null,
        also_unit: valueKind === "number" && combine ? alsoUnit : null,
        value_words: valueKind === "words" ? words.trim() : null,
        built_before: whole(cond.built_before), built_on_or_after: whole(cond.built_on_or_after),
        units_at_least: whole(cond.units_at_least), units_at_most: whole(cond.units_at_most),
        owner_occupied: cond.owner_occupied === "" ? null : cond.owner_occupied === "yes",
        owner_kind: cond.owner_kind === "" ? null : cond.owner_kind,
        precedence: Number(cond.precedence || "0"),
        effective_from: from, effective_to: until || null,
        citation, url, checked_on: checkedOn, reviewed_by: reviewer, exceptions: orNull(exceptions),
        supersedes: correcting?.rule_id ?? null,
      };
      return record("/api/jurisdiction-rules", body, () => (correcting ? "Rule corrected." : "Rule added and confirmed."));
    }}>
      <p className="text-sm text-ink-muted">
        A rule you read in the statute&apos;s own text, cited and dated. It holds for every property of the company in
        that place, and you confirm it as you add it. Exceptions the PMS can&apos;t see go in words: the screens show
        them and warn, never refuse.
      </p>
      <Select label="Of" id="rule-level" value={level} disabled={correcting !== null}
        onChange={(e) => setLevel(e.target.value as Level["level"])}>
        {levels.map((lv) => <option key={lv.level} value={lv.level}>{`${lv.name} (${lv.level})`}</option>)}
      </Select>
      <Select label="Rule" id="rule-kind" value={kind} disabled={correcting !== null}
        onChange={(e) => pickKind(e.target.value as RuleKind)}>
        {KIND_WORDS.map(([k, w]) => <option key={k} value={k}>{w}</option>)}
      </Select>
      <Select label="Its value" id="rule-value-kind" value={valueKind}
        onChange={(e) => setValueKind(e.target.value as "number" | "none" | "words")}>
        <option value="number">A number</option>
        <option value="none">No limit</option>
        <option value="words">In words (no number to check)</option>
      </Select>
      {valueKind === "number" && (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Number" id="rule-number" inputMode="decimal" required value={number} onChange={(e) => setNumber(e.target.value)} />
          <Select label="Unit" id="rule-unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
            {units.map(([u, w]) => <option key={u} value={u}>{w}</option>)}
          </Select>
        </div>
      )}
      {valueKind === "number" && (kind === "deposit_cap" || kind === "late_fee_cap") && (
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Or a second number" id="rule-combine" value={combine} onChange={(e) => setCombine(e.target.value)}>
            <option value="">None</option>
            <option value="lesser">the lesser of the two</option>
            <option value="greater">the greater of the two</option>
          </Select>
          {combine && (
            <>
              <Field label="Second number" id="rule-also" inputMode="decimal" required value={also} onChange={(e) => setAlso(e.target.value)} />
              <Select label="Its unit" id="rule-also-unit" value={alsoUnit} onChange={(e) => setAlsoUnit(e.target.value)}>
                {units.map(([u, w]) => <option key={u} value={u}>{w}</option>)}
              </Select>
            </>
          )}
        </div>
      )}
      {valueKind === "words" && (
        <Field label="The rule in words" id="rule-words" required maxLength={1000} value={words} onChange={(e) => setWords(e.target.value)} />
      )}
      <Field label="In force from" id="rule-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
      <Field label="Until (optional: the first day it no longer holds)" id="rule-until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
      <Field label="Citation" id="rule-citation" required maxLength={300} value={citation} onChange={(e) => setCitation(e.target.value)} />
      <Field label="The statute's page (https)" id="rule-url" required maxLength={500} value={url} onChange={(e) => setUrl(e.target.value)} />
      <Field label="Read in the statute on" id="rule-checked" type="date" required value={checkedOn} onChange={(e) => setCheckedOn(e.target.value)} />
      <Field label="Read by" id="rule-reviewer" required maxLength={100} value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
      <Field label="Exceptions the PMS can't see (optional)" id="rule-exceptions" maxLength={1000} value={exceptions} onChange={(e) => setExceptions(e.target.value)} />
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1 text-sm font-medium text-ink">Only for properties (optional)</legend>
        <div className="flex flex-wrap gap-3">
          <Field label="Built before (year)" id="rule-built-before" inputMode="numeric" maxLength={4} value={cond.built_before} onChange={setC("built_before")} />
          <Field label="Built in or after (year)" id="rule-built-after" inputMode="numeric" maxLength={4} value={cond.built_on_or_after} onChange={setC("built_on_or_after")} />
          <Field label="At least this many units" id="rule-units-least" inputMode="numeric" value={cond.units_at_least} onChange={setC("units_at_least")} />
          <Field label="At most this many units" id="rule-units-most" inputMode="numeric" value={cond.units_at_most} onChange={setC("units_at_most")} />
        </div>
        <div className="flex flex-wrap gap-3">
          <Select label="An owner lives there" id="rule-occupied" value={cond.owner_occupied} onChange={setC("owner_occupied")}>
            <option value="">Either</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </Select>
          <Select label="The owner is" id="rule-owner-kind" value={cond.owner_kind} onChange={setC("owner_kind")}>
            <option value="">Either</option>
            <option value="person">A person</option>
            <option value="company">A company</option>
          </Select>
          <Field label="Precedence (higher wins in its place)" id="rule-precedence" inputMode="numeric" value={cond.precedence} onChange={setC("precedence")} />
        </div>
      </fieldset>
      <div><Button type="submit">{correcting ? "Correct the rule" : "Add the rule"}</Button></div>
      {correcting && correcting.effective_from && (
        <p className="text-sm text-ink-muted">{`Corrects the rule in force from ${usDate(correcting.effective_from)}: ${correcting.words}.`}</p>
      )}
    </Form>
  );
}
