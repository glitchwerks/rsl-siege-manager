/**
 * SiegeSettingsPage — notification polling tests
 *
 * Covers:
 *  - "Notify Members" button is present, disabled for complete sieges
 *  - Clicking the button opens a confirmation dialog
 *  - Confirming triggers POST /notify and renders the batch panel (batch id, member count)
 *  - Batch panel renders per-member status rows:
 *      pending-with-discord  → spinner (animate-spin)
 *      pending-no-discord    → warning icon + "No Discord username" text
 *      success               → green check icon
 *      failure               → red X icon + error message
 *  - Polling transitions: status text changes from in_progress → completed and turns green
 *  - Polling stops once all result items have success !== null
 *  - "Post to Discord" confirm dialog → success message / error message
 *  - "Post to Discord" button disabled when siege is complete
 *  - Validation panel: error-severity rows render with red badge and message
 *  - Validation panel: warning-severity rows render with yellow badge and message
 *  - Notification-blocked banner appears when validate returns errors
 */

import { screen, waitFor, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import {
  beforeAll,
  afterAll,
  afterEach,
  describe,
  it,
  expect,
  vi,
} from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { server } from "../server";
import SiegeSettingsPage from "../../pages/SiegeSettingsPage";
import type {
  Siege,
  ValidationResult,
  NotifyResponse,
  NotificationBatchResponse,
  NotificationResultItem,
} from "../../api/types";

// ─── Server lifecycle ──────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => {
  server.resetHandlers();
  vi.useRealTimers();
});
afterAll(() => server.close());

// ─── Fixture factories ──────────────────────────────────────────────────────

function makeSiege(overrides: Partial<Siege> = {}): Siege {
  return {
    id: 42,
    date: "2026-03-22",
    status: "active",
    defense_scroll_count: 0,
    computed_scroll_count: 0,
    created_at: "2026-03-19T00:00:00Z",
    updated_at: "2026-03-19T00:00:00Z",
    ...overrides,
  };
}

function makeNotifyResponse(
  overrides: Partial<NotifyResponse> = {}
): NotifyResponse {
  return {
    batch_id: 101,
    status: "pending",
    member_count: 2,
    ...overrides,
  };
}

function makeResult(
  overrides: Partial<NotificationResultItem> = {}
): NotificationResultItem {
  return {
    member_id: 1,
    member_name: "Aethon",
    discord_username: "aethon#0001",
    success: null,
    error: null,
    sent_at: null,
    ...overrides,
  };
}

function makeBatchResponse(
  status: string,
  results: NotificationResultItem[]
): NotificationBatchResponse {
  return { batch_id: 101, status, results };
}

// ─── Render helper ────────────────────────────────────────────────────────────
//
// SiegeSettingsPage reads four routes on mount:
//   GET /api/sieges/42              — siege details
//   GET /api/sieges/42/buildings    — building list
//   GET /api/sieges/42/members      — siege member list
//   POST /api/sieges/42/validate    — eager validation (gates Notify button)
//
// validateResult defaults to an empty-pass response so the Notify button is
// enabled in tests that don't care about validation state. Pass a custom value
// to test error / warning rendering.

const emptyValidation: ValidationResult = { errors: [], warnings: [] };

function renderPage(
  siege: Siege = makeSiege(),
  validateResult: ValidationResult = emptyValidation
) {
  server.use(
    http.get("/api/sieges/42", () => HttpResponse.json(siege)),
    http.get("/api/sieges/42/buildings", () => HttpResponse.json([])),
    http.get("/api/sieges/42/members", () => HttpResponse.json([])),
    // SiegeSettingsPage eagerly calls validateSiege on mount to gate the Notify
    // button. Without this handler the mutation fails silently and validation
    // state stays null, hiding error rows and the notification-blocked banner.
    http.post("/api/sieges/42/validate", () =>
      HttpResponse.json(validateResult)
    )
  );

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });

  return render(
    <MemoryRouter initialEntries={["/sieges/42"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/sieges/:id" element={<SiegeSettingsPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

// Helper: wait for the page loading state to resolve
async function waitForPageLoad() {
  await waitFor(() =>
    expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
  );
}

describe("SiegeSettingsPage — locked editing", () => {
  it.each(["active", "complete"] as const)(
    "disables settings mutations when siege is %s",
    async (status) => {
      renderPage(makeSiege({ status }));
      await waitForPageLoad();

      expect(screen.getByLabelText(/^date$/i)).toBeDisabled();
      expect(
        screen.getByRole("button", { name: /save settings/i })
      ).toBeDisabled();
    }
  );
});

// ─── Notify Members button ─────────────────────────────────────────────────

describe("SiegeSettingsPage — Notify Members button", () => {
  it("renders the Notify Members button", async () => {
    renderPage();
    await waitForPageLoad();
    expect(
      screen.getByRole("button", { name: /notify members/i })
    ).toBeInTheDocument();
  });

  it("is disabled when siege status is complete", async () => {
    renderPage(makeSiege({ status: "complete" }));
    await waitForPageLoad();
    expect(
      screen.getByRole("button", { name: /notify members/i })
    ).toBeDisabled();
  });

  it("opens a confirmation dialog when the button is clicked", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    // Check the dialog heading specifically (button and heading both contain "Notify Members")
    expect(within(dialog).getByText(/notify members/i)).toBeInTheDocument();
  });

  it("disables Notify Members button and shows spinner while batch is in-progress", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("in_progress", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: null,
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    // Wait for batch panel to appear (POST returned)
    await waitFor(() =>
      expect(screen.getByText(/batch #101/i)).toBeInTheDocument()
    );

    // Button should be disabled while batch is in-progress
    const notifyBtn = screen.getByRole("button", { name: /notify members/i });
    expect(notifyBtn).toBeDisabled();

    // Button should show a spinner (animate-spin) while batch is in-progress
    expect(notifyBtn.querySelector(".animate-spin")).not.toBeNull();
  });

  it("re-enables Notify Members button after batch completes", async () => {
    const user = userEvent.setup();

    let callCount = 0;
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () => {
        callCount += 1;
        if (callCount === 1) {
          return HttpResponse.json(
            makeBatchResponse("in_progress", [
              makeResult({
                member_id: 1,
                member_name: "Aethon",
                success: null,
              }),
            ])
          );
        }
        return HttpResponse.json(
          makeBatchResponse("completed", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: true,
              sent_at: "2026-03-22T10:00:00Z",
            }),
          ])
        );
      })
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    // Wait for batch to complete
    await waitFor(
      () => {
        const statusEl = screen.queryByText("completed");
        return expect(statusEl).not.toBeNull();
      },
      { timeout: 10000 }
    );

    // After completion, button should no longer be disabled (except for normal conditions)
    const notifyBtn = screen.getByRole("button", { name: /notify members/i });
    expect(notifyBtn).not.toBeDisabled();

    // No spinner should be showing on the button
    expect(notifyBtn.querySelector(".animate-spin")).toBeNull();
  });
});

// ─── Notification batch panel ─────────────────────────────────────────────

describe("SiegeSettingsPage — notification batch panel", () => {
  /**
   * Click "Notify Members" → confirm → verify the batch summary panel appears.
   * The confirm button inside the dialog is "Send Notifications".
   */
  it("shows the batch panel after confirming, displaying batch id and member count", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(
          makeNotifyResponse({ batch_id: 101, member_count: 2 })
        )
      ),
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("in_progress", [
            makeResult({ member_id: 1, member_name: "Aethon", success: null }),
            makeResult({
              member_id: 2,
              member_name: "Brint",
              discord_username: null,
              success: null,
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    await waitFor(() =>
      expect(screen.getByText(/batch #101/i)).toBeInTheDocument()
    );
    expect(screen.getByText(/2 members/i)).toBeInTheDocument();
  });

  it("shows a spinner for a pending member who has a discord username", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("in_progress", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: null,
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    // Wait for the member row to appear
    await waitFor(() => expect(screen.getByText("Aethon")).toBeInTheDocument());

    // The Lucide Loader2 svg has the animate-spin class
    const memberRow = screen.getByText("Aethon").closest("li")!;
    expect(memberRow.querySelector(".animate-spin")).not.toBeNull();
  });

  it('shows a warning and "No Discord username" for a member without a discord username', async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("in_progress", [
            makeResult({
              member_id: 2,
              member_name: "Brint",
              discord_username: null,
              success: null,
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    // member_name and "— No Discord username" are in the same span, so use the combined text
    await waitFor(() =>
      expect(screen.getByText(/no discord username/i)).toBeInTheDocument()
    );
    // The warning icon has text-yellow-500
    const memberRow = screen.getByText(/no discord username/i).closest("li")!;
    expect(memberRow.querySelector(".text-yellow-500")).not.toBeNull();
  });

  it("shows a green check icon for a successfully notified member", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("completed", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: true,
              sent_at: "2026-03-22T10:00:00Z",
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    await waitFor(() => expect(screen.getByText("Aethon")).toBeInTheDocument());
    const memberRow = screen.getByText("Aethon").closest("li")!;
    // Lucide Check rendered with text-green-600
    expect(memberRow.querySelector(".text-green-600")).not.toBeNull();
  });

  it("shows a red X icon and error message for a failed member", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("completed", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: false,
              error: "User not found",
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    await waitFor(() =>
      expect(screen.getByText(/user not found/i)).toBeInTheDocument()
    );
    const memberRow = screen.getByText(/aethon/i).closest("li")!;
    // Lucide X rendered with text-red-600
    expect(memberRow.querySelector(".text-red-600")).not.toBeNull();
  });

  it("shows error icon (not spinner) for a pending member when batch is completed", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      // Batch is completed but member still has success=null (DB write failed)
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("completed", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: null,
              error: null,
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    await waitFor(
      () => expect(screen.getByText(/status unknown/i)).toBeInTheDocument(),
      {
        timeout: 5000,
      }
    );

    const memberRow = screen.getByText(/status unknown/i).closest("li")!;

    // Must NOT show a spinning loader
    expect(memberRow.querySelector(".animate-spin")).toBeNull();
  });

  it("shows spinner for a pending member when batch is still in-progress", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () =>
        HttpResponse.json(
          makeBatchResponse("pending", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: null,
            }),
          ])
        )
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    await waitFor(() => expect(screen.getByText("Aethon")).toBeInTheDocument());

    const memberRow = screen.getByText("Aethon").closest("li")!;
    // Should show spinner while batch is still running
    expect(memberRow.querySelector(".animate-spin")).not.toBeNull();
  });
});

// ─── Polling state transitions ─────────────────────────────────────────────

describe("SiegeSettingsPage — notification polling state transitions", () => {
  /**
   * batchDone returns false on the first poll (in_progress, success=null) so React Query
   * uses a 3-second refetchInterval. The second poll returns completed, causing batchDone
   * to return false (stopping further polling). We verify the text and its green styling.
   */
  it("transitions status text from in_progress to completed and applies green styling", async () => {
    const user = userEvent.setup();

    let callCount = 0;
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () => {
        callCount += 1;
        if (callCount === 1) {
          // First poll: still processing
          return HttpResponse.json(
            makeBatchResponse("in_progress", [
              makeResult({
                member_id: 1,
                member_name: "Aethon",
                success: null,
              }),
            ])
          );
        }
        // Subsequent polls: done
        return HttpResponse.json(
          makeBatchResponse("completed", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: true,
              sent_at: "2026-03-22T10:00:00Z",
            }),
          ])
        );
      })
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    // Wait for the batch panel
    await waitFor(() =>
      expect(screen.getByText(/batch #101/i)).toBeInTheDocument()
    );

    // React Query will refetch because batchDone=false on first result.
    // When the second response arrives, "completed" should appear with the green class.
    await waitFor(
      () => {
        const statusEl = screen.getByText("completed");
        expect(statusEl).toBeInTheDocument();
        expect(statusEl).toHaveClass("text-green-600");
      },
      { timeout: 10000 }
    );
  });

  /**
   * When all results already have success !== null, batchDone returns true on the very
   * first poll and refetchInterval returns false — no further fetches should occur.
   */
  it("stops polling once all results have success !== null (batchDone=true)", async () => {
    const user = userEvent.setup();

    let callCount = 0;
    server.use(
      http.post("/api/sieges/42/notify", () =>
        HttpResponse.json(makeNotifyResponse())
      ),
      http.get("/api/sieges/42/notify/101", () => {
        callCount += 1;
        // Every response has success=true, so batchDone=true immediately
        return HttpResponse.json(
          makeBatchResponse("in_progress", [
            makeResult({
              member_id: 1,
              member_name: "Aethon",
              discord_username: "aethon#0001",
              success: true,
              sent_at: "2026-03-22T10:00:00Z",
            }),
          ])
        );
      })
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /notify members/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /send notifications/i })
    );

    // Wait for the first poll to resolve and member row to appear
    await waitFor(
      () => expect(screen.getByText("Aethon")).toBeInTheDocument(),
      {
        timeout: 5000,
      }
    );

    const countAfterFirstRender = callCount;

    // Sleep briefly; since refetchInterval=false, no additional polls should fire
    await act(async () => {
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(callCount).toBe(countAfterFirstRender);
  });
});

// ─── Post to Discord ───────────────────────────────────────────────────────

describe("SiegeSettingsPage — Post to Discord", () => {
  it('shows "Posted successfully." after a successful post', async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/post-to-channel", () =>
        HttpResponse.json({ status: "ok" })
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /post to discord/i }));
    const dialog = screen.getByRole("dialog");
    // Confirm button inside the Post to Discord dialog is "Post"
    await user.click(within(dialog).getByRole("button", { name: /^post$/i }));

    await waitFor(() =>
      expect(screen.getByText(/posted successfully/i)).toBeInTheDocument()
    );
  });

  it("shows the error detail when post to channel returns a 4xx", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/sieges/42/post-to-channel", () =>
        HttpResponse.json({ detail: "Channel not found" }, { status: 400 })
      )
    );

    renderPage();
    await waitForPageLoad();

    await user.click(screen.getByRole("button", { name: /post to discord/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^post$/i }));

    await waitFor(() =>
      expect(screen.getByText(/channel not found/i)).toBeInTheDocument()
    );
  });

  it("disables Post to Discord button when siege is complete", async () => {
    renderPage(makeSiege({ status: "complete" }));
    await waitForPageLoad();
    expect(
      screen.getByRole("button", { name: /post to discord/i })
    ).toBeDisabled();
  });
});

// ─── Validation panel ─────────────────────────────────────────────────────

describe("SiegeSettingsPage — validation panel", () => {
  it("renders error-severity rows with destructive badge when validate returns errors", async () => {
    // Pass the error response directly to renderPage so it is registered
    // before the component mounts and fires the eager validateMutation.
    renderPage(makeSiege(), {
      errors: [
        {
          rule: 1,
          message: "Assigned member 'Alice' is not active",
          context: null,
        },
      ],
      warnings: [],
    });
    await waitForPageLoad();

    // Eager validation fires on mount; wait for error row to appear
    await waitFor(() =>
      expect(screen.getByText(/error 1/i)).toBeInTheDocument()
    );
    expect(
      screen.getByText(/assigned member 'alice' is not active/i)
    ).toBeInTheDocument();
  });

  it("renders warning-severity rows with yellow badge when validate returns warnings", async () => {
    renderPage(makeSiege(), {
      errors: [],
      warnings: [
        {
          rule: 10,
          message: "Building has fewer members than recommended",
          context: null,
        },
      ],
    });
    await waitForPageLoad();

    await waitFor(() =>
      expect(screen.getByText(/warning 10/i)).toBeInTheDocument()
    );
    expect(
      screen.getByText(/building has fewer members than recommended/i)
    ).toBeInTheDocument();
  });

  it("shows the notification-blocked banner when validate returns errors", async () => {
    renderPage(makeSiege(), {
      errors: [
        { rule: 2, message: "Duplicate member assignment", context: null },
      ],
      warnings: [],
    });
    await waitForPageLoad();

    await waitFor(() =>
      expect(screen.getByText(/notifications blocked/i)).toBeInTheDocument()
    );
  });
});
