"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export type AuthUser = { id: string; email: string };

type AuthState =
  { status: "loading" } | { status: "signed-in"; user: AuthUser } | { status: "signed-out" };

type AuthContextValue = { state: AuthState; signOut: () => Promise<void> };

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Tracks the browser session for the app shell. The proxy already keeps signed-out
 * visitors away; this covers what happens later in the tab: sign-out elsewhere, an
 * expired refresh token, or signing out from the user menu.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setState(
        session
          ? { status: "signed-in", user: { id: session.user.id, email: session.user.email ?? "" } }
          : { status: "signed-out" },
      );
    });
    return () => subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (state.status !== "signed-out") return;
    queryClient.clear(); // never show one user's cached data to the next
    const next = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
    router.replace(`/login${next}`);
  }, [state.status, pathname, queryClient, router]);

  const signOut = useCallback(async () => {
    await getSupabaseBrowserClient().auth.signOut();
  }, []);

  const value = useMemo(() => ({ state, signOut }), [state, signOut]);
  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthContextValue {
  const value = use(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
