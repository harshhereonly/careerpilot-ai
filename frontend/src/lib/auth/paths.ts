/** Pages anyone can open. Everything else needs a session (enforced in src/proxy.ts). */
export const PUBLIC_PATHS = ["/login", "/register", "/forgot-password", "/auth"];

/** Pages that make no sense once signed in; they redirect to the app. */
export const SIGNED_OUT_ONLY_PATHS = ["/login", "/register", "/forgot-password"];

function matches(pathname: string, prefixes: string[]): boolean {
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

export function isPublicPath(pathname: string): boolean {
  return matches(pathname, PUBLIC_PATHS);
}

export function isSignedOutOnlyPath(pathname: string): boolean {
  return matches(pathname, SIGNED_OUT_ONLY_PATHS);
}

/**
 * Where to go after signing in. Only same-site paths are allowed, so a crafted
 * `?next=https://evil.example` link cannot turn the login page into an open redirect.
 */
export function safeNextPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return "/";
  }
  try {
    const url = new URL(value, "http://localhost");
    if (url.origin !== "http://localhost") return "/";
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}
