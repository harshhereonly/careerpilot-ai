import { CompassIcon } from "lucide-react";
import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-6 bg-muted/40 p-6">
      <div className="flex items-center gap-2 text-lg font-semibold tracking-tight">
        <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <CompassIcon className="size-4" />
        </span>
        CareerPilot AI
      </div>
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
