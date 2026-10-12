import { useEffect, useState } from "react";
import type { Me } from "./api";
import { ApiError, api } from "./api";
import { Notice } from "./components/Notice";
import { ApplicationStatusPage } from "./pages/ApplicationStatusPage";
import { ApplyPage } from "./pages/ApplyPage";
import { BillsPage } from "./pages/BillsPage";
import { ESignPage } from "./pages/ESignPage";
import { InvitePage } from "./pages/InvitePage";
import { OwnerHome } from "./pages/OwnerHome";
import { OwnersPage } from "./pages/OwnersPage";
import { ReconcilePage } from "./pages/ReconcilePage";
import { RentPage } from "./pages/RentPage";
import { PMC_KEY, SignInPage } from "./pages/SignInPage";
import { SetupPage } from "./pages/SetupPage";
import { StaffHome } from "./pages/StaffHome";
import { TenantHome } from "./pages/TenantHome";
import { navigate, usePath } from "./router";

export function App() {
  const path = usePath();
  if (path === "/invite") return <InvitePage />;
  // Applicants have no login: their links carry what lets them in.
  if (path === "/apply") return <ApplyPage />;
  if (path === "/application") return <ApplicationStatusPage />;
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
    if (path === "/reconcile") return <ReconcilePage me={me} />;
    return <StaffHome me={me} />;
  }
  return <TenantHome me={me} />;
}
