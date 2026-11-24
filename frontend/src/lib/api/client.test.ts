import { describe, expect, it } from "vitest";

import { toApiError } from "@/lib/api/client";

describe("toApiError", () => {
  it("reads the API's error envelope", async () => {
    const response = Response.json(
      {
        error: {
          code: "token_expired",
          message: "The access token has expired.",
          request_id: "abc123",
        },
      },
      { status: 401 },
    );

    const error = await toApiError(response);

    expect(error.status).toBe(401);
    expect(error.code).toBe("token_expired");
    expect(error.message).toBe("The access token has expired.");
    expect(error.requestId).toBe("abc123");
  });

  it("copes with a body that is not our shape", async () => {
    const error = await toApiError(new Response("<html>Bad gateway</html>", { status: 502 }));

    expect(error.status).toBe(502);
    expect(error.code).toBe("http_error");
  });
});
