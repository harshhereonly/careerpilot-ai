import {
  BarChart3Icon,
  BriefcaseIcon,
  Building2Icon,
  FileTextIcon,
  InboxIcon,
  LayoutDashboardIcon,
  MessagesSquareIcon,
  SettingsIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  title: string;
  href: string;
  icon: LucideIcon;
  description: string;
};

export const NAV_ITEMS: NavItem[] = [
  {
    title: "Overview",
    href: "/",
    icon: LayoutDashboardIcon,
    description: "Your outreach at a glance.",
  },
  {
    title: "Contacts",
    href: "/contacts",
    icon: UsersIcon,
    description: "Recruiters and hiring managers you add yourself or import from CSV.",
  },
  {
    title: "Companies",
    href: "/companies",
    icon: Building2Icon,
    description: "Companies you are targeting and what you know about them.",
  },
  {
    title: "Opportunities",
    href: "/opportunities",
    icon: BriefcaseIcon,
    description: "Internships and roles you are tracking, and your applications.",
  },
  {
    title: "Outreach Queue",
    href: "/outreach",
    icon: InboxIcon,
    description: "Drafted emails waiting for your review. Nothing is sent without your approval.",
  },
  {
    title: "Email Conversations",
    href: "/conversations",
    icon: MessagesSquareIcon,
    description: "Threads with your contacts, replies and follow-ups.",
  },
  {
    title: "Resumes",
    href: "/resumes",
    icon: FileTextIcon,
    description: "Your resumes and the skills extracted from them.",
  },
  {
    title: "Analytics",
    href: "/analytics",
    icon: BarChart3Icon,
    description: "How your outreach is performing.",
  },
  {
    title: "Settings",
    href: "/settings",
    icon: SettingsIcon,
    description: "Your account, appearance and outreach limits.",
  },
];

export function navItemFor(pathname: string): NavItem | undefined {
  if (pathname === "/") return NAV_ITEMS[0];
  return NAV_ITEMS.find((item) => item.href !== "/" && pathname.startsWith(item.href));
}
