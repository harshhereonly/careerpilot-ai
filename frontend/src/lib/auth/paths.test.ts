import { describe, expect, it } from "vitest";

import { isPublicPath, isSignedOutOnlyPath, safeNextPath } from "@/lib/auth/paths";

describe("safeNextPath", () => {
  it.each([
    ["/contacts", "/contacts"],
    ["/contacts?tab=new#top", "/contacts?tab=new#top"],
    ["/reset-password", "/reset-password"],
  ])("keeps same-site path %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    null,
    undefined,
    "",
    "contacts",
    "https://evil.example/phish",
    "//evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "/%2F%2Fevil.example",
  ])("refuses %s and falls back to /", (input) => {
    const result = safeNextPath(input);
    expect(result.startsWith("/")).toBe(true);
    expect(result.startsWith("//")).toBe(false);
    expect(new URL(result, "https://app.example").origin).toBe("https://app.example");
  });
});

describe("route classification", () => {
  it("treats auth pages and the email callback as public", () => {
    for (const path of ["/login", "/register", "/forgot-password", "/auth/callback"]) {
      expect(isPublicPath(path)).toBe(true);
    }
  });

  it("protects the app, including the reset-password step", () => {
    for (const path of ["/", "/contacts", "/settings", "/reset-password", "/loginx", "/authx"]) {
      expect(isPublicPath(path)).toBe(false);
    }
  });

  it("only bounces signed-in users away from sign-in pages", () => {
    expect(isSignedOutOnlyPath("/login")).toBe(true);
    expect(isSignedOutOnlyPath("/auth/callback")).toBe(false);
    expect(isSignedOutOnlyPath("/reset-password")).toBe(false);
  });
});
