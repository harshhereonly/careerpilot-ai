import { describe, expect, it } from "vitest";

import { NAV_ITEMS, navItemFor } from "@/lib/navigation";

describe("navigation", () => {
  it("has the nine sections from the brief, in order", () => {
    expect(NAV_ITEMS.map((item) => item.title)).toEqual([
      "Overview",
      "Contacts",
      "Companies",
      "Opportunities",
      "Outreach Queue",
      "Email Conversations",
      "Resumes",
      "Analytics",
      "Settings",
    ]);
  });

  it("finds the active section, including nested paths", () => {
    expect(navItemFor("/")?.title).toBe("Overview");
    expect(navItemFor("/contacts/123")?.title).toBe("Contacts");
    expect(navItemFor("/nowhere")).toBeUndefined();
  });
});
