import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { isPublicPath, isSignedOutOnlyPath } from "@/lib/auth/paths";
import { missingPublicConfig, publicConfig } from "@/lib/config";

/**
 * Runs before every page: refreshes the Supabase session cookies and keeps signed-out
 * visitors out of the app. This is a convenience gate for pages only. The API verifies
 * the token on every request itself and never relies on this check.
 */
export async function proxy(request: NextRequest) {
  // Without configuration the pages render a setup message instead of redirect loops.
  if (missingPublicConfig().length > 0) return NextResponse.next({ request });

  let response = NextResponse.next({ request });
  const supabase = createServerClient(publicConfig.supabaseUrl, publicConfig.supabaseKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet, headers) => {
        for (const { name, value } of cookiesToSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
        for (const [key, value] of Object.entries(headers)) response.headers.set(key, value);
      },
    },
  });

  // getClaims verifies the token signature (against the public JWKS), not just its presence.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);
  const { pathname, search } = request.nextUrl;

  if (!signedIn && !isPublicPath(pathname)) {
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", `${pathname}${search}`);
    return redirectKeepingCookies(login, response);
  }
  if (signedIn && isSignedOutOnlyPath(pathname)) {
    return redirectKeepingCookies(new URL("/", request.url), response);
  }
  return response;
}

function redirectKeepingCookies(url: URL, from: NextResponse): NextResponse {
  const redirect = NextResponse.redirect(url);
  for (const cookie of from.cookies.getAll()) redirect.cookies.set(cookie);
  redirect.headers.set("Cache-Control", "private, no-store");
  return redirect;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
