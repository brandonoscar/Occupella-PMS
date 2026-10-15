import { useEffect, useState } from "react";
import type { Me } from "./api";
import { ApiError, api } from "./api";
import { Notice } from "./components/Notice";
import { ApplicationStatusPage } from "./pages/ApplicationStatusPage";
import { ApplyPage } from "./pages/ApplyPage";
import { BillsPage } from "./pages/BillsPage";
import { BudgetsPage } from "./pages/BudgetsPage";
import { ESignPage } from "./pages/ESignPage";
import { InvitePage } from "./pages/InvitePage";
import { ListingsPage } from "./pages/ListingsPage";
import { OwnerHome } from "./pages/OwnerHome";
import { DocumentsPage } from "./pages/DocumentsPage";
import { MoveOutPage } from "./pages/MoveOutPage";
import { OwnersPage } from "./pages/OwnersPage";
import { JournalPage } from "./pages/JournalPage";
import { ReconcilePage } from "./pages/ReconcilePage";
import { RentPage } from "./pages/RentPage";
import { PMC_KEY, SignInPage } from "./pages/SignInPage";
import { SetupPage } from "./pages/SetupPage";
import { StaffHome } from "./pages/StaffHome";
import { StaffPage } from "./pages/StaffPage";
import { SwitchPage } from "./pages/SwitchPage";
import { TenantHome } from "./pages/TenantHome";
import { TodayPage } from "./pages/TodayPage";
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
    if (path === "/setup") return <SetupPage me={me} />;
    if (path === "/rent") return <RentPage me={me} />;
    if (path === "/bills") return <BillsPage me={me} />;
    if (path === "/owners") return <OwnersPage me={me} />;
    if (path === "/budgets") return <BudgetsPage me={me} />;
    if (path === "/move-out") return <MoveOutPage me={me} />;
    if (path === "/documents") return <DocumentsPage me={me} />;
    if (path === "/vendors") return <VendorsPage me={me} />;
    if (path === "/reconcile") return <ReconcilePage me={me} />;
    if (path === "/journal") return <JournalPage me={me} />;
    if (path === "/switching") return <SwitchPage me={me} />;
    if (path === "/staff") return <StaffPage me={me} />;
    if (path === "/today") return <TodayPage me={me} />;
    return <StaffHome me={me} />;
  }
  return <TenantHome me={me} />;
}
