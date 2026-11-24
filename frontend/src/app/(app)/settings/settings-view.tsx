"use client";

import { MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import { useTheme } from "next-themes";

import { useAuth } from "@/components/auth/auth-provider";
import { ModulePage } from "@/components/layout/module-page";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/queries";
import { NAV_ITEMS } from "@/lib/navigation";

const item = NAV_ITEMS.find((entry) => entry.href === "/settings")!;

const THEMES = [
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
  { value: "system", label: "System", icon: MonitorIcon },
] as const;

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[10rem_1fr] sm:items-center">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-sm">{children}</dd>
    </div>
  );
}

export function SettingsView() {
  const me = useMe();
  const { signOut } = useAuth();
  const { theme, setTheme } = useTheme();

  return (
    <ModulePage item={item}>
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>
            Loaded from the CareerPilot API, through row-level security.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {me.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {me.error instanceof ApiError ? me.error.message : "Could not load your account."}
              </AlertDescription>
            </Alert>
          ) : (
            <dl className="grid gap-3">
              <Row label="Email">{me.data?.email ?? <Skeleton className="h-4 w-48" />}</Row>
              <Row label="User ID">
                {me.data ? (
                  <code className="text-xs">{me.data.id}</code>
                ) : (
                  <Skeleton className="h-4 w-64" />
                )}
              </Row>
              <Row label="Member since">
                {me.data ? (
                  new Date(me.data.created_at).toLocaleDateString()
                ) : (
                  <Skeleton className="h-4 w-24" />
                )}
              </Row>
            </dl>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
          <CardDescription>Applies to this browser.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2" role="group" aria-label="Theme">
          {THEMES.map(({ value, label, icon: Icon }) => (
            <Button
              key={value}
              variant={theme === value ? "default" : "outline"}
              aria-pressed={theme === value}
              onClick={() => setTheme(value)}
            >
              <Icon />
              {label}
            </Button>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Session</CardTitle>
          <CardDescription>
            Outreach limits and data export arrive with their modules.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={() => void signOut()}>
            Sign out
          </Button>
        </CardContent>
      </Card>
    </ModulePage>
  );
}
