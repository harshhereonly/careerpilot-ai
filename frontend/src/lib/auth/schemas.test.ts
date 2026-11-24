import { describe, expect, it } from "vitest";

import { loginSchema, registerSchema, resetPasswordSchema } from "@/lib/auth/schemas";

describe("auth form validation", () => {
  it("rejects a malformed email", () => {
    expect(loginSchema.safeParse({ email: "not-an-email", password: "x" }).success).toBe(false);
  });

  it("requires a password to sign in", () => {
    expect(loginSchema.safeParse({ email: "a@b.test", password: "" }).success).toBe(false);
  });

  it("requires 8+ characters and matching confirmation to register", () => {
    const short = registerSchema.safeParse({
      email: "a@b.test",
      password: "short",
      confirmPassword: "short",
    });
    const mismatch = registerSchema.safeParse({
      email: "a@b.test",
      password: "long-enough-1",
      confirmPassword: "long-enough-2",
    });
    const ok = registerSchema.safeParse({
      email: "a@b.test",
      password: "long-enough-1",
      confirmPassword: "long-enough-1",
    });

    expect(short.success).toBe(false);
    expect(mismatch.success).toBe(false);
    expect(mismatch.error?.issues[0]?.path).toEqual(["confirmPassword"]);
    expect(ok.success).toBe(true);
  });

  it("applies the same rules to a password reset", () => {
    expect(resetPasswordSchema.safeParse({ password: "abc", confirmPassword: "abc" }).success).toBe(
      false,
    );
  });
});
