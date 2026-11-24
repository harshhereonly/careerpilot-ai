import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { publicConfig } from "@/lib/config";

let client: SupabaseClient | undefined;

/** One client per browser tab. The session lives in cookies so the proxy can read it too. */
export function getSupabaseBrowserClient(): SupabaseClient {
  client ??= createBrowserClient(publicConfig.supabaseUrl, publicConfig.supabaseKey);
  return client;
}
