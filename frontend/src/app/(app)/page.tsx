import type { Metadata } from "next";

import { Dashboard } from "./dashboard";

export const metadata: Metadata = { title: "Overview" };

export default function OverviewPage() {
  return <Dashboard />;
}
