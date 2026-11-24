import { TriangleAlertIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { missingPublicConfig } from "@/lib/config";

/** Shown instead of a broken form when frontend/.env.local is incomplete. */
export function SetupNotice() {
  const missing = missingPublicConfig();
  if (missing.length === 0) return null;
  return (
    <Alert variant="destructive">
      <TriangleAlertIcon />
      <AlertTitle>Setup needed</AlertTitle>
      <AlertDescription>
        Add {missing.join(" and ")} to frontend/.env.local and restart the dev server. See
        docs/SUPABASE_SETUP.md.
      </AlertDescription>
    </Alert>
  );
}
