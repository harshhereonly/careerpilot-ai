"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { apiFetch } from "@/lib/api/client";

export const meSchema = z.object({
  id: z.string(),
  email: z.string(),
  full_name: z.string().nullable(),
  created_at: z.string(),
});
export type Me = z.infer<typeof meSchema>;

export const summarySchema = z.object({
  total_contacts: z.number(),
  emails_sent: z.number(),
  replies_received: z.number(),
  positive_replies: z.number(),
  interviews: z.number(),
  rejections: z.number(),
  pending_drafts: z.number(),
  follow_ups_due: z.number(),
});
export type Summary = z.infer<typeof summarySchema>;

export const queryKeys = {
  me: ["me"] as const,
  summary: ["analytics", "summary"] as const,
};

export function useMe() {
  return useQuery({ queryKey: queryKeys.me, queryFn: () => apiFetch("/api/v1/me", meSchema) });
}

export function useSummary() {
  return useQuery({
    queryKey: queryKeys.summary,
    queryFn: () => apiFetch("/api/v1/analytics/summary", summarySchema),
  });
}
