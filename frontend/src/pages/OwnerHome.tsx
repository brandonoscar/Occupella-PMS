import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { lastMonth, money } from "../money";
import type { StatementData } from "../statement";
import { group } from "../statement";
import { StatementView } from "../components/StatementView";
import { OwnerBills } from "./OwnerBills";
import { OwnerDocuments } from "./OwnerDocuments";
import { OwnerIncomeStatement } from "./OwnerIncomeStatement";
import { OwnerPayCard } from "./OwnerPayCard";
import { OwnerYearEnd } from "./OwnerYearEnd";
import { Form } from "../components/Form";
import { ManagerCard } from "./ManagerCard";
import { MyContactCard } from "../components/ContactCard";
import { MessagesCard } from "../components/MessagesCard";
import type { NavEntry } from "../components/Shell";
import { OwnerPropertyCard, OwnerRepairsCard } from "./OwnerPortalPages";
import {
  Building2,
  FileBarChart,
  FolderOpen,
  Home as HomeIcon,
  MessagesSquare,
  Receipt,
  UserRound,
  Wrench,
} from "lucide-react";

type Property = { account_id: string; property_id: string; name: string; bank: string; balance: string };

// Cells take the table style's padding (less of it on a phone); only their alignment is set here.
const CELL = "text-left";
const AMOUNT = "text-right";

// The owner portal's sections (the portals' second version): each a page of its own in the
// sidebar. A property of theirs opens at /properties/<id>.
export type OwnerSection =
  | "overview" | "properties" | "statements" | "bills" | "repairs" | "documents" | "messages" | "profile";
const OWNER_NAV: NavEntry[] = [
  { href: "/", label: "Overview", icon: HomeIcon },
  { href: "/properties", label: "Properties", icon: Building2 },
  { href: "/statements", label: "Statements and reports", icon: FileBarChart },
  { href: "/bills", label: "Bills", icon: Receipt },
  { href: "/repairs", label: "Repairs", icon: Wrench },
  { href: "/documents", label: "Documents", icon: FolderOpen },
  { href: "/messages", label: "Messages", icon: MessagesSquare },
  { href: "/profile", label: "Profile", icon: UserRound },
];
export const OWNER_SECTIONS: Record<string, OwnerSection> = {
  "/": "overview",
  "/properties": "properties",
  "/statements": "statements",
  "/bills": "bills",
  "/repairs": "repairs",
  "/documents": "documents",
  "/messages": "messages",
  "/profile": "profile",
};
const HEADERS: Record<OwnerSection, [string, string]> = {
  overview: ["Your properties", "What each property holds today, money you add, and the bills waiting for you."],
  properties: ["Properties", "Each property's units, who rents them, and what is vacant."],
  statements: ["Statements and reports", "Your statement for any days, your year-end package and income statement."],
  bills: ["Bills", "The bills over your approval limit, waiting for you to approve or decline."],
  repairs: ["Repairs", "The work your manager opened at your properties, and how it's going."],
  documents: ["Documents", "What your manager shared with you."],
  messages: ["Messages", "Write to your manager and read their answers."],
  profile: ["Your profile", "Your contact details, and your manager's."],
};

export function OwnerHome({ me, section = "overview", propertyId }: {
  me: Me;
  section?: OwnerSection;
  propertyId?: string;
}) {
  const [properties, setProperties] = useState<Property[] | null>(null);
  const [period, setPeriod] = useState(lastMonth(new Date()));
  const [statement, setStatement] = useState<StatementData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Property[]>("/api/owner/properties").then(setProperties).catch(show);
  }, []);

  useEffect(() => {
    if (section === "statements") load(period);
  }, [section]); // the first statement only, on its own section; "Show" loads the next

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  function load(range: { start: string; end: string }) {
    setError("");
    setStatement(null);
    const query = new URLSearchParams(range).toString();
    api.get<StatementData>(`/api/owner/statement?${query}`).then(setStatement).catch(show);
  }

  const download = (format: string) =>
    `/api/owner/statement/download?${new URLSearchParams({ ...period, format })}`;
  const grouped = statement ? group(statement) : null;

  const [heading, description] = HEADERS[section];
  if (propertyId) {
    return (
      <Shell me={me} nav={OWNER_NAV}>
        <PageHeader title="Property" description="Its units, who rents each, and what is vacant." />
        <p className="mb-4 text-sm">
          <a className="text-iris hover:text-iris-hover hover:underline" href="/properties">All your properties</a>
        </p>
        <OwnerPropertyCard propertyId={propertyId} />
      </Shell>
    );
  }

  const holds = (
    <Card title={section === "properties" ? "Your properties" : "What each property holds today"}>
      {properties === null ? (
        <Notice tone="info">Loading</Notice>
      ) : properties.length === 0 ? (
        <Notice tone="info">No properties are held for you in trust.</Notice>
      ) : (
        <table className="w-full text-base">
          <thead className="text-sm text-ink-subtle">
            <tr>
              <th className={CELL}>Property</th>
              <th className={CELL}>Trust account</th>
              <th className="text-right">Balance</th>
            </tr>
          </thead>
          <tbody>
            {properties.map((p) => (
              <tr key={p.account_id} className="border-t border-line">
                <td className={CELL}>
                  <a className="font-medium text-iris hover:text-iris-hover hover:underline"
                    href={`/properties/${p.property_id}`}>{p.name}</a>
                </td>
                <td className={`${CELL} text-ink-muted`}>{p.bank}</td>
                <td className={AMOUNT}>{money(p.balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );

  return (
    <Shell me={me} nav={OWNER_NAV}>
      <PageHeader title={heading} description={description} />
      {section === "overview" && (
        <>
          {holds}
          <OwnerPayCard />
          <OwnerBills />
          <ManagerCard />
        </>
      )}
      {section === "properties" && holds}
      {section === "statements" && (
        <>
          <Card title="Statement">
            <Form
              className="mb-6 flex flex-wrap items-end gap-4"
              onSubmit={(event) => {
                event.preventDefault();
                load(period);
              }}
            >
              <Field label="From" id="start" type="date" value={period.start} required
                onChange={(e) => setPeriod({ ...period, start: e.target.value })} />
              <Field label="To" id="end" type="date" value={period.end} required
                onChange={(e) => setPeriod({ ...period, end: e.target.value })} />
              <Button type="submit">Show</Button>
              <div className="flex gap-2">
                <LinkButton href={download("pdf")}>PDF</LinkButton>
                <LinkButton href={download("csv")}>CSV</LinkButton>
                <LinkButton href={download("xlsx")}>Excel</LinkButton>
              </div>
            </Form>
            <Notice tone="error">{error}</Notice>
            {grouped && <StatementView grouped={grouped} />}
          </Card>
          <OwnerYearEnd />
          <OwnerIncomeStatement />
        </>
      )}
      {section === "bills" && <OwnerBills />}
      {section === "repairs" && <OwnerRepairsCard />}
      {section === "documents" && <OwnerDocuments />}
      {section === "messages" && (
        <MessagesCard path="/api/owner/messages" company={me.pmc_name} person={me.name} />
      )}
      {section === "profile" && (
        <>
          <MyContactCard name={me.name} who="owner" />
          <ManagerCard />
        </>
      )}
    </Shell>
  );
}
