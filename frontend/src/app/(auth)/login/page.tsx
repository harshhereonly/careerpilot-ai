import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  // The form reads ?next= and ?error=, which needs a Suspense boundary under Cache Components.
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
