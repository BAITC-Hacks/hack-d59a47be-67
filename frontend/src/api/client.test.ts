import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, clearSession, createApiClient, getCsrf } from "./client";
import type { CompletionRequest } from "./types";

const identity = {
  id: "TEST_USER",
  username: "test",
  role: "employee",
  employee_id: "TEST_EMPLOYEE",
};
const session = {
  user: identity,
  csrf_token: "test-csrf",
  expires_at: "2026-10-01T12:00:00Z",
};
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const body: CompletionRequest = {
  expected_state_version: 4,
  event_id: "TEST_EVENT",
  mode: "completion",
};

beforeEach(() => {
  clearSession();
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  clearSession();
});

describe("real HTTP client", () => {
  it("uses HttpOnly cookie sessions, saves only CSRF, and sends it on protected mutations", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(json({ state_version: 5, completion: {} }));
    const client = createApiClient({ fetch: fetcher });
    await client.login("test", "private password");
    await client.complete("employee/with space", body, "stable-operation-key");

    const [loginUrl, loginInit] = fetcher.mock.calls[0];
    expect(loginUrl).toBe("/api/auth/login");
    expect(loginInit).toMatchObject({ method: "POST", credentials: "include" });
    expect(loginInit?.headers).not.toHaveProperty("Origin");
    expect(loginInit?.headers).not.toHaveProperty("X-CSRF-Token");
    expect(getCsrf()).toBe("test-csrf");
    expect(Object.values(sessionStorage)).toEqual(["test-csrf"]);
    expect(localStorage.length).toBe(0);

    const [completeUrl, completeInit] = fetcher.mock.calls[1];
    expect(completeUrl).toBe(
      "/api/employees/employee%2Fwith%20space/completions",
    );
    expect(completeInit).toMatchObject({
      credentials: "include",
      method: "POST",
      body: JSON.stringify(body),
      headers: {
        "X-CSRF-Token": "test-csrf",
        "Idempotency-Key": "stable-operation-key",
      },
    });
  });

  it("preserves body, key and old revision when the caller retries a lost completion response", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(session))
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(
        json({ state_version: 5, completion: { completion_id: "saved" } }),
      );
    const client = createApiClient({ fetch: fetcher });
    await client.login("test", "password");
    await expect(
      client.complete("employee", body, "same-key"),
    ).rejects.toMatchObject({ code: "NETWORK_ERROR" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    await client.complete("employee", body, "same-key");
    expect(fetcher.mock.calls[2][1]?.body).toBe(fetcher.mock.calls[1][1]?.body);
    expect(fetcher.mock.calls[2][1]?.headers).toEqual(
      fetcher.mock.calls[1][1]?.headers,
    );
    expect(body.expected_state_version).toBe(4);
  });

  it("rejects unavailable real API without switching to synthetic data", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new TypeError("connection refused"));
    const client = createApiClient({ fetch: fetcher });
    await expect(client.employee("person")).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      status: 0,
    });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(localStorage.length).toBe(0);
  });

  it("shows validation field metadata and request ID without exposing the body", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(
        json(
          {
            code: "invalid_source",
            message: "Source fields failed validation",
            request_id: "req-42",
            details: {
              file_index: 0,
              errors: [
                { location: ["employees", 1, "grade"], type: "literal_error" },
              ],
            },
          },
          422,
        ),
      );
    const client = createApiClient({ fetch: fetcher });
    await client.login("test", "secret");
    await expect(
      client.importFiles({
        dry_run: true,
        files: [
          {
            source_filename: "employees.json",
            source_format: "json",
            content: "sensitive content",
          },
        ],
      }),
    ).rejects.toMatchObject({
      name: "ApiError",
      code: "invalid_source",
      requestId: "req-42",
      message: expect.stringContaining("Файлы не прошли проверку"),
      details: { file_index: 0 },
    });
  });

  it("clears expired sessions and never sends a mutation when CSRF was lost", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(
        json(
          {
            code: "UNAUTHENTICATED",
            message: "expired",
            request_id: "req",
            details: {},
          },
          401,
        ),
      );
    const client = createApiClient({ fetch: fetcher });
    await client.login("test", "password");
    await expect(client.me()).rejects.toMatchObject({ status: 401 });
    expect(getCsrf()).toBeNull();
    await expect(
      client.complete("employee", body, "key"),
    ).rejects.toMatchObject({ code: "CSRF_MISSING" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("retains session on failed logout, then clears it after server confirmation", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(session))
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(json({ status: "logged_out" }));
    const client = createApiClient({ fetch: fetcher });
    await client.login("test", "password");
    await expect(client.logout()).rejects.toBeInstanceOf(ApiError);
    expect(getCsrf()).toBe("test-csrf");
    await client.logout();
    expect(getCsrf()).toBeNull();
  });

  it("aborts requests within the timeout and exposes a recoverable error", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    const request = createApiClient({
      fetch: fetcher,
      timeoutMs: 500,
    }).health();
    const assertion = expect(request).rejects.toMatchObject({
      code: "TIMEOUT",
      status: 0,
    });
    await vi.advanceTimersByTimeAsync(500);
    await assertion;
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it("rejects an HTML success page instead of using it as a profile", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("<html>proxy</html>", { status: 200 }));
    await expect(
      createApiClient({ fetch: fetcher }).employee("person"),
    ).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it.each([
    [401, "UNAUTHENTICATED"],
    [403, "CSRF_FAILED"],
  ])(
    "does not clear a new session after a delayed %s %s from the previous session",
    async (status, code) => {
      let release!: (value: Response) => void;
      const oldResponse = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(json(session))
        .mockReturnValueOnce(oldResponse)
        .mockResolvedValueOnce(
          json({ ...session, csrf_token: "new-session-csrf" }),
        )
        .mockResolvedValueOnce(json({ state_version: 5, completion: {} }));
      const oldClient = createApiClient({ fetch: fetcher });
      await oldClient.login("first-account", "password");
      // A GET has no CSRF header, but still belongs to its original session.
      const oldRequest =
        status === 401
          ? oldClient.employee("employee")
          : oldClient.complete("employee", body, "old-key");
      const assertion = expect(oldRequest).rejects.toMatchObject({
        status,
        code,
      });
      const currentClient = createApiClient({ fetch: fetcher });
      await currentClient.login("second-account", "password");
      release(json({ code, request_id: "old-request", details: {} }, status));
      await assertion;
      expect(getCsrf()).toBe("new-session-csrf");
      await currentClient.complete("employee", body, "new-key");
      expect(fetcher.mock.calls[3][1]?.headers).toMatchObject({
        "X-CSRF-Token": "new-session-csrf",
      });
    },
  );

  it("does not clear a new session when the previous logout finishes late", async () => {
    let release!: (value: Response) => void;
    const oldLogout = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(session))
      .mockReturnValueOnce(oldLogout)
      .mockResolvedValueOnce(
        json({ ...session, csrf_token: "new-session-csrf" }),
      );
    const oldClient = createApiClient({ fetch: fetcher });
    await oldClient.login("first-account", "password");
    const logout = oldClient.logout();
    const currentClient = createApiClient({ fetch: fetcher });
    await currentClient.login("second-account", "password");
    release(json({ status: "logged_out" }));
    await logout;
    expect(getCsrf()).toBe("new-session-csrf");
  });
});
