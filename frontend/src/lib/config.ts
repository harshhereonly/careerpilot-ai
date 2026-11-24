// NEXT_PUBLIC_* values are inlined at build time, so each must be read by its literal name.
export const publicConfig = {
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
  supabaseKey: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "",
  apiBaseUrl: (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/+$/, ""),
};

/** Names of required public settings that are empty, for a clear setup message instead of a crash. */
export function missingPublicConfig(): string[] {
  const missing: string[] = [];
  if (!publicConfig.supabaseUrl) missing.push("NEXT_PUBLIC_SUPABASE_URL");
  if (!publicConfig.supabaseKey) missing.push("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY");
  return missing;
}
