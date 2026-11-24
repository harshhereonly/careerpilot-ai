import type { ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";
import type { NavItem } from "@/lib/navigation";

/** Header plus an honest empty state for a module whose features are not built yet. */
export function ModulePage({ item, children }: { item: NavItem; children?: ReactNode }) {
  return (
    <div className="grid gap-6">
      <div className="grid gap-1">
        <h2 className="text-2xl font-semibold tracking-tight">{item.title}</h2>
        <p className="text-sm text-muted-foreground">{item.description}</p>
      </div>
      {children ?? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <span className="flex size-12 items-center justify-center rounded-full bg-muted">
              <item.icon className="size-5 text-muted-foreground" />
            </span>
            <p className="font-medium">Nothing here yet</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              This section is not built yet. It will show your real data once its module is
              implemented in a later phase.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
