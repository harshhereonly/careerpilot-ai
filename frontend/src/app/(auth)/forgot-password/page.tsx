"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { FormField } from "@/components/auth/form-field";
import { SetupNotice } from "@/components/setup-notice";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { forgotPasswordSchema, type ForgotPasswordValues } from "@/lib/auth/schemas";
import { missingPublicConfig } from "@/lib/config";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export default function ForgotPasswordPage() {
  const [sent, setSent] = useState(false);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<ForgotPasswordValues>({ resolver: zodResolver(forgotPasswordSchema) });

  async function onSubmit({ email }: ForgotPasswordValues) {
    const { error } = await getSupabaseBrowserClient().auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/callback?next=/reset-password`,
    });
    // Rate limits and outages are worth showing; "no such user" is never reported by Supabase.
    if (error) {
      setError("root", { message: error.message || "Could not send the email. Try again." });
      return;
    }
    setSent(true);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reset your password</CardTitle>
        <CardDescription>
          {sent
            ? "If an account exists for that address, a reset link is on its way. Open it in this browser."
            : "Enter your email and we will send you a link to choose a new password."}
        </CardDescription>
      </CardHeader>
      {sent ? null : (
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <CardContent className="grid gap-4">
            <SetupNotice />
            {errors.root?.message ? (
              <Alert variant="destructive">
                <AlertDescription>{errors.root.message}</AlertDescription>
              </Alert>
            ) : null}
            <FormField
              id="email"
              label="Email"
              type="email"
              autoComplete="email"
              error={errors.email?.message}
              {...register("email")}
            />
          </CardContent>
          <CardFooter className="mt-4">
            <Button
              type="submit"
              className="w-full"
              disabled={isSubmitting || missingPublicConfig().length > 0}
            >
              {isSubmitting ? "Sending…" : "Send reset link"}
            </Button>
          </CardFooter>
        </form>
      )}
      <CardFooter className={sent ? undefined : "pt-0"}>
        <Link href="/login" className="text-sm underline-offset-4 hover:underline">
          Back to sign in
        </Link>
      </CardFooter>
    </Card>
  );
}
