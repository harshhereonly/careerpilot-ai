import { z } from "zod";

import { publicConfig } from "@/lib/config";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

const errorEnvelope = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    request_id: z.string().nullable().optional(),
  }),
});

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Turns any failed response into an ApiError, whether or not it has our error shape. */
export async function toApiError(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null);
  const parsed = errorEnvelope.safeParse(body);
  if (parsed.success) {
    const { code, message, request_id } = parsed.data.error;
    return new ApiError(response.status, code, message, request_id);
  }
  return new ApiError(response.status, "http_error", `Request failed (${response.status}).`);
}

/**
 * Calls the CareerPilot API with the signed-in user's access token and validates the
 * response against `schema`, so a contract change fails loudly instead of rendering junk.
 */
export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const { data } = await getSupabaseBrowserClient().auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "unauthenticated", "You are not signed in.");

  let response: Response;
  try {
    response = await fetch(`${publicConfig.apiBaseUrl}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers, Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the CareerPilot API.");
  }
  if (!response.ok) throw await toApiError(response);

  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) {
    throw new ApiError(response.status, "invalid_response", "The API sent an unexpected response.");
  }
  return parsed.data;
}
