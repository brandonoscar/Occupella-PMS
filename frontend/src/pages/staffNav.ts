import {
  ArrowLeftRight,
  BookOpen,
  Building2,
  CalendarCheck,
  DoorOpen,
  FileText,
  FolderOpen,
  Landmark,
  Receipt,
  Settings,
  UserCog,
  Users,
} from "lucide-react";
import type { NavItem } from "../components/Shell";

// Staff's sections, in the sidebar of every staff page.
export const STAFF_NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: Building2 },
  { href: "/today", label: "Today", icon: CalendarCheck },
  { href: "/rent", label: "Rent", icon: Receipt },
  { href: "/bills", label: "Bills", icon: FileText },
  { href: "/owners", label: "Owners", icon: Users },
  { href: "/move-out", label: "Move-out", icon: DoorOpen },
  { href: "/documents", label: "Documents", icon: FolderOpen },
  { href: "/reconcile", label: "Month-end", icon: Landmark },
  { href: "/journal", label: "Journal", icon: BookOpen },
  { href: "/switching", label: "Switching", icon: ArrowLeftRight },
  { href: "/staff", label: "Staff", icon: UserCog },
  { href: "/setup", label: "Setup", icon: Settings },
];
