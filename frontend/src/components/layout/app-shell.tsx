"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { AuthProvider, useAuth } from "@/components/auth/auth-provider";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { UserMenu } from "@/components/layout/user-menu";
import { SetupNotice } from "@/components/setup-notice";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Skeleton } from "@/components/ui/skeleton";
import { missingPublicConfig } from "@/lib/config";
import { navItemFor } from "@/lib/navigation";

function Header() {
  const title = navItemFor(usePathname())?.title ?? "CareerPilot AI";
  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
      <h1 className="truncate text-sm font-medium">{title}</h1>
      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}

function Content({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  // Render nothing user-specific until the session is known; signed-out users are redirected.
  if (state.status !== "signed-in") {
    return (
      <div className="grid gap-4 p-6" aria-busy="true">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  return <div className="mx-auto w-full max-w-6xl p-4 md:p-6">{children}</div>;
}

export function AppShell({ children }: { children: ReactNode }) {
  // The Supabase client cannot be created without its settings; explain instead of crashing.
  if (missingPublicConfig().length > 0) {
    return (
      <main className="mx-auto grid max-w-lg gap-4 p-6 pt-24">
        <SetupNotice />
      </main>
    );
  }
  return (
    <AuthProvider>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset>
          <Header />
          <main id="main">
            <Content>{children}</Content>
          </main>
        </SidebarInset>
      </SidebarProvider>
    </AuthProvider>
  );
}
