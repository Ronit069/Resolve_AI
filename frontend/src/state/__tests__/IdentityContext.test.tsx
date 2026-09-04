import { render, screen, act } from "@testing-library/react";
import { StrictMode, useEffect } from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { IdentityProvider, useIdentity } from "../IdentityContext";
import { getQueueMetrics } from "../../api/client";

/**
 * D-01 regression tests.
 *
 * These deliberately do NOT mock "../../api/client" — the whole point is to
 * exercise the real request() function and its real module-level identity
 * state, wired to the real IdentityProvider, so the test fails the same way
 * production did (a fetch reaching the network with no X-User-Id header)
 * if the initialization race ever comes back. Only the network boundary
 * (global.fetch) is mocked.
 */

const USER_ID_KEY = "resolveai.dev.userId";
const KNOWN_USER_ID = "11111111-1111-1111-1111-111111111111";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
}

const METRICS_BODY = {
  generated_at: "2026-01-01T00:00:00Z",
  queue_age: { active_item_count: 0, average_age_seconds: null, min_age_seconds: null, max_age_seconds: null },
  near_deadline: { threshold_hours: 24, near_deadline_count: 0, expired_count: 0 },
  review_turnaround: {
    completed_item_count: 0,
    average_turnaround_seconds: null,
    min_turnaround_seconds: null,
    max_turnaround_seconds: null,
  },
};

/** Fires a real client.ts call from its own mount effect — the same shape
 * every real page component (RiskCommandCenter, DisputeQueue, ...) uses. */
function FetchOnMount() {
  useEffect(() => {
    getQueueMetrics();
  }, []);
  return <div>consumer mounted</div>;
}

function IdentityLabel() {
  const { userId } = useIdentity();
  return <div data-testid="identity-label">{userId ?? "none"}</div>;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  window.localStorage.clear();
  fetchMock = vi.fn().mockImplementation(() => jsonResponse(METRICS_BODY));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function headersOf(call: unknown[]): Record<string, string> {
  const init = call[1] as RequestInit | undefined;
  return (init?.headers as Record<string, string>) ?? {};
}

describe("D-01: identity initialization vs. first authenticated request", () => {
  it("attaches the correct X-User-Id on the very first request fired by a child's mount effect", async () => {
    window.localStorage.setItem(USER_ID_KEY, KNOWN_USER_ID);

    render(
      <IdentityProvider>
        <FetchOnMount />
      </IdentityProvider>
    );

    await screen.findByText("consumer mounted");

    // The race, if present, produces a first call with NO X-User-Id header.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const headers = headersOf(fetchMock.mock.calls[0]);
    expect(headers["X-User-Id"]).toBe(KNOWN_USER_ID);
  });

  it("never lets a request through with an undefined/null/empty user id when no identity is set", async () => {
    // localStorage intentionally left empty.
    render(
      <IdentityProvider>
        <FetchOnMount />
      </IdentityProvider>
    );

    await screen.findByText("consumer mounted");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const headers = headersOf(fetchMock.mock.calls[0]);
    // No identity means no header at all — never a stringified "null"/"undefined"/"".
    expect(headers["X-User-Id"]).toBeUndefined();
  });

  it("holds under React.StrictMode too — every request that reaches the network carries the header, not just a later one", async () => {
    window.localStorage.setItem(USER_ID_KEY, KNOWN_USER_ID);

    render(
      <StrictMode>
        <IdentityProvider>
          <FetchOnMount />
        </IdentityProvider>
      </StrictMode>
    );

    await screen.findByText("consumer mounted");

    expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
    for (const call of fetchMock.mock.calls) {
      expect(headersOf(call)["X-User-Id"]).toBe(KNOWN_USER_ID);
    }
  });

  it("propagates an identity switch (e.g. Login) to the API client synchronously, before the next request", async () => {
    function Switcher() {
      const { setIdentity } = useIdentity();
      return (
        <button
          onClick={() => {
            setIdentity(KNOWN_USER_ID, "APPROVER");
            // Fired in the SAME handler, immediately after setIdentity —
            // proving the API client is updated synchronously rather than
            // waiting for a subsequent render's effect to flush.
            getQueueMetrics();
          }}
        >
          switch
        </button>
      );
    }

    render(
      <IdentityProvider>
        <Switcher />
        <IdentityLabel />
      </IdentityProvider>
    );

    expect(screen.getByTestId("identity-label")).toHaveTextContent("none");

    await act(async () => {
      screen.getByRole("button", { name: "switch" }).click();
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(headersOf(fetchMock.mock.calls[0])["X-User-Id"]).toBe(KNOWN_USER_ID);
  });

  it("existing successful authenticated behavior still works end to end", async () => {
    window.localStorage.setItem(USER_ID_KEY, KNOWN_USER_ID);

    render(
      <IdentityProvider>
        <FetchOnMount />
      </IdentityProvider>
    );

    await screen.findByText("consumer mounted");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/observability/queue-metrics"),
      expect.objectContaining({ headers: expect.objectContaining({ "X-User-Id": KNOWN_USER_ID }) })
    );
  });
});
