import { useEffect, useState } from "react";
import type { Me } from "./api";
import { ApiError, api } from "./api";
import { Notice } from "./components/Notice";
import { ApplicationStatusPage } from "./pages/ApplicationStatusPage";
import { ApplyPage } from "./pages/ApplyPage";
import { BillsPage } from "./pages/BillsPage";
import { BudgetsPage } from "./pages/BudgetsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ESignPage } from "./pages/ESignPage";
import { InvitePage } from "./pages/InvitePage";
import { ListingsPage } from "./pages/ListingsPage";
import { OwnerHome } from "./pages/OwnerHome";
import { DocumentsPage } from "./pages/DocumentsPage";
import { MoveOutPage } from "./pages/MoveOutPage";
import { GeneralLedgerPage } from "./pages/GeneralLedgerPage";
import { OwnerDrawsPage } from "./pages/OwnerDrawsPage";
import { OwnerPage, OwnersPage } from "./pages/OwnersPage";
import { TenantPage, TenantsPage } from "./pages/TenantsPages";
import { JournalPage } from "./pages/JournalPage";
import { ApplicantsPage, DraftLeasesPage, WorkOrdersPage } from "./pages/LeasingPages";
import { LoginsPage } from "./pages/LoginsPage";
import { BankingPage, ReconcilePage } from "./pages/ReconcilePage";
import { ReportsPage } from "./pages/ReportsPage";
import { RentPage } from "./pages/RentPage";
import { LeasePage, PropertiesPage, PropertyPage, UnitPage } from "./pages/RentalsPages";
import { PMC_KEY, SignInPage } from "./pages/SignInPage";
import { SetupPage } from "./pages/SetupPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StaffPage } from "./pages/StaffPage";
import { SwitchPage } from "./pages/SwitchPage";
import { TenantHome } from "./pages/TenantHome";
import { VendorsPage } from "./pages/VendorsPage";
import { navigate, usePath } from "./router";

export function App() {
  const path = usePath();
  if (path === "/invite") return <InvitePage />;
  // Applicants have no login: their links carry what lets them in.
  if (path === "/apply") return <ApplyPage />;
  if (path === "/application") return <ApplicationStatusPage />;
  // A PMC's public vacancy page; nobody signs in to read it.
  if (path === "/listings") return <ListingsPage />;
  // The fake e-sign partner's page; a signer's link carries what lets them in.
  if (path === "/esign") return <ESignPage />;
  if (path === "/sign-in") return <SignInPage />;
  return <Home path={path} />;
}

// Who is signed in decides the page; nobody signed in goes to sign in. Staff have sections.
function Home({ path }: { path: string }) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .get<Me>("/api/me")
      .then(setMe)
      .catch((failed) => {
        if (failed instanceof ApiError && failed.status === 401) {
          const pmc = localStorage.getItem(PMC_KEY);
          navigate(pmc ? `/sign-in?pmc=${pmc}` : "/sign-in");
        } else {
          setError(failed instanceof ApiError ? failed.detail : "the server can't be reached");
        }
      });
  }, []);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!me) return null;
  if (me.kind === "owner") return <OwnerHome me={me} />;
  if (me.kind === "staff") {
    const page = STAFF_PAGES[path];
    if (page) return page(me);
    for (const [pattern, record] of RECORDS) {
      const found = pattern.exec(path);
      if (found?.[1]) return record(me, found[1]);
    }
    return <DashboardPage me={me} />;
  }
  return <TenantHome me={me} />;
}

// Staff's pages by path (ADR 0005's menu); any other path is the Dashboard.
const STAFF_PAGES: Record<string, (me: Me) => React.ReactNode> = {
  "/properties": (me) => <PropertiesPage me={me} />,
  "/rent-roll": (me) => <RentPage me={me} />,
  "/tenants": (me) => <TenantsPage me={me} />,
  "/owners": (me) => <OwnersPage me={me} />,
  "/move-out": (me) => <MoveOutPage me={me} />,
  "/applicants": (me) => <ApplicantsPage me={me} />,
  "/draft-leases": (me) => <DraftLeasesPage me={me} />,
  "/banking": (me) => <BankingPage me={me} />,
  "/bills": (me) => <BillsPage me={me} />,
  "/general-ledger": (me) => <GeneralLedgerPage me={me} />,
  "/journal": (me) => <JournalPage me={me} />,
  "/owner-draws": (me) => <OwnerDrawsPage me={me} />,
  "/budgets": (me) => <BudgetsPage me={me} />,
  "/work-orders": (me) => <WorkOrdersPage me={me} />,
  "/vendors": (me) => <VendorsPage me={me} />,
  "/files": (me) => <DocumentsPage me={me} />,
  "/reports": (me) => <ReportsPage me={me} />,
  "/settings": (me) => <SettingsPage me={me} />,
  "/settings/staff": (me) => <StaffPage me={me} />,
  "/settings/logins": (me) => <LoginsPage me={me} />,
  "/settings/setup": (me) => <SetupPage me={me} />,
  "/settings/import": (me) => <SwitchPage me={me} />,
};

// A record's own page, by its address (ADR 0005).
const RECORDS: [RegExp, (me: Me, id: string) => React.ReactNode][] = [
  [/^\/properties\/([0-9a-f-]{36})$/, (me, id) => <PropertyPage me={me} id={id} />],
  [/^\/units\/([0-9a-f-]{36})$/, (me, id) => <UnitPage me={me} id={id} />],
  [/^\/leases\/([0-9a-f-]{36})$/, (me, id) => <LeasePage me={me} id={id} />],
  [/^\/tenants\/([0-9a-f-]{36})$/, (me, id) => <TenantPage me={me} id={id} />],
  [/^\/owners\/([0-9a-f-]{36})$/, (me, id) => <OwnerPage me={me} id={id} />],
  [/^\/bank-accounts\/([0-9a-f-]{36})$/, (me, id) => <ReconcilePage me={me} id={id} />],
];
