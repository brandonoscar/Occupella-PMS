import { DoorOpen } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import type { RowAction } from "../components/RowMenu";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { MAX_BYTES, titleOf, toBase64, typeOf } from "../documents";
import { latest } from "../latest";
import type { ListingPhoto, OpenUnit } from "../leasing";
import { money, usDate } from "../money";
import type { Paged } from "../rentals";
import { scopeParams, useScope } from "../scope";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

type Panel = { kind: "list" | "link" | "photos"; unit: OpenUnit };

const TITLES: Record<Panel["kind"], string> = {
  list: "List on the public page",
  link: "Open an application link",
  photos: "Listing photos",
};

// How long a listing has been up, counting the day it went up.
export function daysListed(days: number | null): string {
  return days === null ? "" : days === 1 ? "1 day" : `${days} days`;
}

// The public vacancy page's address, to give out.
export function publicPage(pmcId: string, origin = window.location.origin): string {
  return `${origin}/listings?pmc=${pmcId}`;
}

// Leasing → Listings (ADR 0006, Buildium's): each unit coming free within 90 days (or with an open
// application link), when, at what rent, and whether it is on the public vacancy page and for how
// many days; List puts it there (opening its link if it has none) and Unlist takes it off. Photos
// keeps the unit's photos the public page shows (migration 0087). Its ⋯ menu opens a link to post
// elsewhere, or closes the open one. Nothing is sent to listing sites.
export function RentalListingsPage({ me }: { me: Me }) {
  const [scope, setScope] = useScope();
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<OpenUnit> | null>(null);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  // What the latest save or copy did: each says it in turn.
  const [said, setSaid] = useState("");
  const [runs, setRuns] = useState(0);
  const { error: refused, record } = useRecord(() => setRuns((n) => n + 1), setSaid);

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ page: String(number), ...scopeParams(scope) });
    api.get<Paged<OpenUnit>>(`/api/leasing/vacancies?${query}`).then(run.keep(setPage))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [scope, number, runs]);

  async function copy() {
    const address = publicPage(me.pmc_id);
    try {
      await navigator.clipboard.writeText(address);
      setSaid("Copied the public page's link.");
    } catch {
      setSaid(`The public page's link: ${address}`);
    }
  }

  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Listings" actions={
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => void copy()}>Copy public page link</Button>
          <LinkButton href={`/listings?pmc=${me.pmc_id}`}>Public page</LinkButton>
        </div>
      } />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }} />
      <Said>{said}</Said>
      {panel === null && <Notice tone="error">{refused}</Notice>}
      {page === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={DoorOpen} title="No unit comes free in the next 90 days." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Unit</th>
                  <th className="text-left">Available</th>
                  <th className="text-right">Rent</th>
                  <th className="text-right">Applications</th>
                  <th className="text-left">Public page</th>
                  <th className="text-right">Days listed</th>
                  <th className="text-right">Photos</th>
                  <th><span className="sr-only">List</span></th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((unit) => {
                  const name = `${unit.property}, ${unit.unit}`;
                  const rent = unit.link_rent ?? unit.last_rent;
                  const actions: RowAction[] = [
                    { label: "Open an application link", onSelect: () => setPanel({ kind: "link", unit }) },
                  ];
                  if (unit.link_id) {
                    const link = unit.link_id;
                    actions.push({
                      label: "Close its link",
                      danger: true,
                      confirm: `Close the application link for ${name}? It takes no more applications${unit.listing_id ? " and leaves the public page" : ""}.`,
                      onSelect: () => record(`/api/leasing/links/${link}/close`, {}, () => `Closed the link for ${name}.`),
                    });
                  }
                  return (
                    <tr key={unit.unit_id} aria-label={name}>
                      <td><GoLink href={`/units/${unit.unit_id}`} className={LINK}>{name}</GoLink></td>
                      <td className="whitespace-nowrap">
                        {unit.available_on ? usDate(unit.available_on) : <span className="text-ink-muted">Leased</span>}
                      </td>
                      <td className="text-right">{rent ? money(rent) : ""}</td>
                      <td className="text-right">{unit.applications}</td>
                      <td>
                        {unit.listing_id
                          ? <Badge tone="positive" data-testid="listed">Listed</Badge>
                          : <Badge data-testid="listed">Not listed</Badge>}
                      </td>
                      <td className="text-right whitespace-nowrap">{daysListed(unit.days_listed)}</td>
                      <td className="text-right">
                        <Button variant="secondary" size="sm" aria-label={`Photos, ${name}`}
                          onClick={() => setPanel({ kind: "photos", unit })}>
                          {unit.photos}
                        </Button>
                      </td>
                      <td className="text-right">
                        {unit.listing_id ? (
                          <Button variant="secondary" size="sm"
                            onClick={() => record(`/api/leasing/listings/${unit.listing_id}/remove`, {},
                              () => `Took ${name} off the public page.`)}>
                            Unlist
                          </Button>
                        ) : (
                          <Button variant="secondary" size="sm" onClick={() => setPanel({ kind: "list", unit })}>List</Button>
                        )}
                      </td>
                      <td className="text-right"><RowMenu label={name} actions={actions} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
      {panel !== null && (
        <SidePanel title={TITLES[panel.kind]} onClose={() => setPanel(null)}>
          <p className="text-base text-ink">{panel.unit.property}, {panel.unit.unit}</p>
          {panel.kind === "list" ? <ListForm unit={panel.unit} record={recordThenClose} />
            : panel.kind === "link" ? <LinkForm unit={panel.unit} record={record} />
            : <PhotosForm unit={panel.unit} onChange={() => setRuns((n) => n + 1)} />}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// Lists a unit on the public page: its open link, or a new one at the rent asked.
function ListForm({ unit, record }: { unit: OpenUnit; record: Recorder }) {
  const [rent, setRent] = useState(unit.last_rent ?? "");
  const [description, setDescription] = useState("");
  const name = `${unit.property}, ${unit.unit}`;
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const listing = { description: description.trim() || null };
      if (unit.link_id) {
        void record(`/api/leasing/links/${unit.link_id}/listing`, listing,
          () => `Listed ${name} at ${money(unit.link_rent ?? "0")}.`);
      } else {
        void record("/api/leasing/links", { unit_id: unit.unit_id, monthly_rent: rent, listing },
          () => `Listed ${name} at ${money(rent)}.`);
      }
    }}>
      {unit.link_id ? (
        <p className="text-sm text-ink-muted">At {money(unit.link_rent ?? "0")} a month, as its open link asks.</p>
      ) : (
        <Field label="Rent a month" id="list-rent" inputMode="decimal" required value={rent}
          onChange={(e) => setRent(e.target.value)} />
      )}
      <label htmlFor="list-description" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Description (optional)
        <textarea id="list-description" rows={4} maxLength={2000} value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
      </label>
      <div><Button type="submit">List it</Button></div>
    </Form>
  );
}

// Opens an application link to post anywhere; its address is shown this once.
function LinkForm({ unit, record }: { unit: OpenUnit; record: Recorder }) {
  const [rent, setRent] = useState(unit.link_rent ?? unit.last_rent ?? "");
  const [opened, setOpened] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record<{ url: string }>("/api/leasing/links", { unit_id: unit.unit_id, monthly_rent: rent }, (link) => {
        setOpened(link.url);
        return `Opened a link for ${unit.property}, ${unit.unit}.`;
      });
    }}>
      <Field label="Rent a month" id="link-rent" inputMode="decimal" required value={rent}
        onChange={(e) => setRent(e.target.value)} />
      <div><Button type="submit" disabled={opened !== ""}>Open the link</Button></div>
      {opened && (
        <p className="text-sm text-ink-muted">
          Shown this once:{" "}
          <span className="font-mono break-all text-ink" data-testid="application-link">{opened}</span>
        </p>
      )}
    </Form>
  );
}

// A unit's listing photos (migration 0087): the public page shows them, oldest first, while the
// unit is listed. Each is a JPEG or PNG of at most 5 MB, twelve at most; Remove takes one off.
function PhotosForm({ unit, onChange }: { unit: OpenUnit; onChange: () => void }) {
  const [photos, setPhotos] = useState<ListingPhoto[] | null>(null);
  const [unread, setUnread] = useState("");
  const [picker, setPicker] = useState(0);
  const [problem, setProblem] = useState("");
  const [runs, setRuns] = useState(0);
  const { said, error, record } = useRecord(() => {
    setRuns((n) => n + 1);
    onChange();
  });
  useEffect(() => {
    api.get<ListingPhoto[]>(`/api/leasing/units/${unit.unit_id}/photos`).then(setPhotos)
      .catch((why) => setUnread(why instanceof ApiError ? why.detail : String(why)));
  }, [unit.unit_id, runs]);

  async function add(picked: File | undefined) {
    setProblem("");
    if (!picked) return;
    const type = typeOf(picked);
    if (type === null || type === "application/pdf") return setProblem("A photo is a JPEG or a PNG.");
    if (picked.size > MAX_BYTES) return setProblem("A photo is at most 5 MB.");
    const title = titleOf(picked.name);
    const body = {
      kind: "listing_photo", about_id: unit.unit_id, title, content_type: type,
      content_base64: toBase64(new Uint8Array(await picked.arrayBuffer())),
    };
    if (await record("/api/documents", body, () => `Added ${title}.`)) setPicker((n) => n + 1);
  }

  if (photos === null) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  return (
    <div className="flex flex-col gap-3">
      {photos.length === 0 ? (
        <p className="text-sm text-ink-subtle">No photo yet. The public page shows the ones added here.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-3" aria-label={`Listing photos, ${unit.property}, ${unit.unit}`}>
          {photos.map((photo) => (
            <li key={photo.id} className="flex flex-col gap-1">
              <img src={`/api/documents/${photo.id}/file`} alt={photo.title} className="h-32 w-full rounded-sm object-cover" />
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate text-ink-muted">{photo.title}</span>
                <Button variant="ghost" size="sm" aria-label={`Remove ${photo.title}`}
                  onClick={() => record(`/api/documents/${photo.id}/remove`, {}, () => `Removed ${photo.title}.`)}>
                  Remove
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <label htmlFor={`photo-${unit.unit_id}`} className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Add a photo (JPEG or PNG)
        <input key={picker} id={`photo-${unit.unit_id}`} type="file" accept=".jpg,.jpeg,.png" className="text-sm text-ink"
          onChange={(e) => void add(e.target.files?.[0])} />
      </label>
      <Notice tone="error">{problem}</Notice>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}
