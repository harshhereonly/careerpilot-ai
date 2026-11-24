import type { Metadata } from "next";

import { ModulePage } from "@/components/layout/module-page";
import { NAV_ITEMS } from "@/lib/navigation";

export const metadata: Metadata = { title: "Outreach Queue" };

const item = NAV_ITEMS.find((entry) => entry.href === "/outreach")!;

export default function Page() {
  return <ModulePage item={item} />;
}
