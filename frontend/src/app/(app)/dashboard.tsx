"use client";

import {
  CalendarClockIcon,
  CircleXIcon,
  FileClockIcon,
  MailIcon,
  MessageSquareReplyIcon,
  SmileIcon,
  UsersIcon,
  VideoIcon,
  type LucideIcon,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { useSummary, type Summary } from "@/lib/api/queries";

const METRICS: { key: keyof Summary; label: string; icon: LucideIcon }[] = [
  { key: "total_contacts", label: "Total contacts", icon: UsersIcon },
  { key: "emails_sent", label: "Emails sent", icon: MailIcon },
  { key: "replies_received", label: "Replies received", icon: MessageSquareReplyIcon },
  { key: "positive_replies", label: "Positive replies", icon: SmileIcon },
  { key: "interviews", label: "Interviews", icon: VideoIcon },
  { key: "rejections", label: "Rejections", icon: CircleXIcon },
  { key: "pending_drafts", label: "Pending drafts", icon: FileClockIcon },
  { key: "follow_ups_due", label: "Follow-ups due", icon: CalendarClockIcon },
];

const CHARTS = [
  "Outreach activity",
  "Reply rate",
  "Positive response rate",
  "Application status",
  "Performance by email template",
];

function describe(error: unknown): string {
  if (error instanceof ApiError && error.code === "network_error") {
    return "The API is not reachable. Start the backend and check NEXT_PUBLIC_API_BASE_URL.";
  }
  if (error instanceof ApiError) return `${error.message} (request ${error.requestId ?? "n/a"})`;
  return "Something went wrong while loading your numbers.";
}

export function Dashboard() {
  const summary = useSummary();

  return (
    <div className="grid gap-6">
      <div className="grid gap-1">
        <h2 className="text-2xl font-semibold tracking-tight">Overview</h2>
        <p className="text-sm text-muted-foreground">
          Every number here is counted from your own records. Nothing is estimated or sampled.
        </p>
      </div>

      {summary.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Could not load your numbers</AlertTitle>
          <AlertDescription>{describe(summary.error)}</AlertDescription>
        </Alert>
      ) : null}

      <section aria-label="Key numbers" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {METRICS.map(({ key, label, icon: Icon }) => (
          <Card key={key} size="sm">
            <CardHeader>
              <CardDescription className="flex items-center gap-2">
                <Icon className="size-4" />
                {label}
              </CardDescription>
              <CardTitle className="text-2xl tabular-nums">
                {summary.isPending ? (
                  <Skeleton className="h-7 w-12" />
                ) : summary.isError ? (
                  "–"
                ) : (
                  summary.data[key]
                )}
              </CardTitle>
            </CardHeader>
          </Card>
        ))}
      </section>

      <section aria-label="Charts" className="grid gap-3 md:grid-cols-2">
        {CHARTS.map((title) => (
          <Card key={title}>
            <CardHeader>
              <CardTitle className="text-base">{title}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex h-40 items-center justify-center rounded-lg border border-dashed text-center text-sm text-muted-foreground">
                No chart yet. It will plot your real activity once outreach and analytics are built.
              </div>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
