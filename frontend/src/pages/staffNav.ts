import {
  BarChart3,
  Building2,
  FolderOpen,
  KeyRound,
  Landmark,
  LayoutDashboard,
  Settings,
  Wrench,
} from "lucide-react";
import type { NavEntry } from "../components/Shell";

// Staff's menu, in the sidebar of every staff page (ADR 0005): what a property manager works on,
// grouped as Buildium groups it, in Occupella's look.
export const STAFF_NAV: NavEntry[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  {
    label: "Rentals",
    icon: Building2,
    items: [
      { href: "/properties", label: "Properties" },
      { href: "/rent-roll", label: "Rent roll" },
      { href: "/tenants", label: "Tenants" },
      { href: "/owners", label: "Owners" },
      { href: "/move-out", label: "Move-outs" },
    ],
  },
  {
    label: "Leasing",
    icon: KeyRound,
    items: [
      { href: "/rental-listings", label: "Listings" },
      { href: "/applicants", label: "Applicants" },
      { href: "/draft-leases", label: "Draft leases" },
      { href: "/renewals", label: "Renewals" },
    ],
  },
  {
    label: "Accounting",
    icon: Landmark,
    items: [
      { href: "/banking", label: "Banking" },
      { href: "/bills", label: "Bills" },
      { href: "/general-ledger", label: "General ledger" },
      { href: "/owner-draws", label: "Owner draws" },
      { href: "/budgets", label: "Budgets" },
    ],
  },
  {
    label: "Maintenance",
    icon: Wrench,
    items: [
      { href: "/work-orders", label: "Work orders" },
      { href: "/vendors", label: "Vendors" },
    ],
  },
  { href: "/files", label: "Files", icon: FolderOpen },
  { href: "/reports", label: "Reports", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: Settings },
];

// Settings' own pages, each a link on /settings.
export const SETTINGS = [
  { href: "/settings/staff", label: "Staff and roles" },
  { href: "/settings/logins", label: "Logins" },
  { href: "/settings/setup", label: "Company setup" },
  { href: "/settings/import", label: "Import from another PMS" },
] as const;
