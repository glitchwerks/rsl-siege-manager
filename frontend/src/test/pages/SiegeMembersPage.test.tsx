/**
 * SiegeMembersPage — Attack Day Preview dialog tests (#243)
 *
 * Covers:
 *  - Symmetric case: both Day 1 and Day 2 have the same number of entries —
 *    modal renders exact cell counts, no padding.
 *  - Asymmetric case (bug repro): Day 1 longer than Day 2 — Day 2 column must
 *    NOT have empty cells padded to match Day 1's row count.
 *  - Empty Day 2: Day 1 has entries, Day 2 has none — Day 2 column renders
 *    zero data cells (only its header).
 *  - Column headers show member counts: "Day 1 (N)" / "Day 2 (N)".
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import { Routes, Route } from "react-router-dom";
import { server } from "../server";
import { renderWithProviders } from "../utils";
import SiegeMembersPage from "../../pages/SiegeMembersPage";
import type { Siege, SiegeMember, AttackDayPreviewResult } from "../../api/types";

// ─── Server lifecycle ──────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ─── Fixture factories ────────────────────────────────────────────────────────

function makeSiege(overrides: Partial<Siege> = {}): Siege {
  return {
    id: 99,
    date: "2026-05-08",
    status: "planning",
    defense_scroll_count: 0,
    computed_scroll_count: 0,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function makeSiegeMember(
  overrides: Partial<SiegeMember> = {}
): SiegeMember {
  return {
    siege_id: 99,
    member_id: 1,
    member_name: "Alpha",
    member_role: "heavy_hitter",
    member_power_level: "gt_25m",
    member_is_active: true,
    attack_day: null,
    has_reserve_set: false,
    attack_day_override: false,
    ...overrides,
  };
}

function makePreview(
  day1MemberIds: number[],
  day2MemberIds: number[]
): AttackDayPreviewResult {
  return {
    assignments: [
      ...day1MemberIds.map((id) => ({ member_id: id, attack_day: 1 })),
      ...day2MemberIds.map((id) => ({ member_id: id, attack_day: 2 })),
    ],
    expires_at: "2026-05-08T23:59:00Z",
  };
}

/** Register per-test MSW handlers for siege + members + attack day preview. */
function registerHandlers(
  siege: Siege,
  members: SiegeMember[],
  preview: AttackDayPreviewResult
) {
  server.use(
    http.get(`/api/sieges/${siege.id}`, () => HttpResponse.json(siege)),
    http.get(`/api/sieges/${siege.id}/members`, () =>
      HttpResponse.json(members)
    ),
    http.post(
      `/api/sieges/${siege.id}/members/auto-assign-attack-day`,
      () => HttpResponse.json(preview)
    )
  );
}

/** Render SiegeMembersPage routed to /sieges/:id/members. */
function renderPage(siegeId = 99) {
  return renderWithProviders(
    <Routes>
      <Route path="/sieges/:id/members" element={<SiegeMembersPage />} />
    </Routes>,
    { initialEntries: [`/sieges/${siegeId}/members`] }
  );
}

// ─── Helper: open the preview dialog ─────────────────────────────────────────

async function openPreviewDialog() {
  const user = userEvent.setup();
  // Wait for the page to finish loading
  await waitFor(() => {
    expect(
      screen.getByRole("button", { name: /auto-assign attack days/i })
    ).toBeInTheDocument();
  });
  await user.click(
    screen.getByRole("button", { name: /auto-assign attack days/i })
  );
  // Wait for the dialog title to confirm the modal is open
  await waitFor(() => {
    expect(
      screen.getByText(/attack day preview/i)
    ).toBeInTheDocument();
  });
  return screen.getByRole("dialog");
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("SiegeMembersPage — Attack Day Preview dialog (no empty cells)", () => {
  it("symmetric: both columns render exact cell counts with no padding", async () => {
    const members = [
      makeSiegeMember({ member_id: 1, member_name: "Alpha" }),
      makeSiegeMember({ member_id: 2, member_name: "Beta" }),
    ];
    const preview = makePreview([1], [2]);
    registerHandlers(makeSiege(), members, preview);
    renderPage();

    const dialog = await openPreviewDialog();

    // Each column should have exactly 1 data cell
    const allCells = within(dialog).getAllByText(/^(Alpha|Beta)$/);
    expect(allCells).toHaveLength(2);
  });

  it("asymmetric (bug repro): Day 1 longer than Day 2 — no empty cells in Day 2", async () => {
    // 3 Day-1 members, 1 Day-2 member
    const members = [
      makeSiegeMember({ member_id: 1, member_name: "Alpha" }),
      makeSiegeMember({ member_id: 2, member_name: "Beta" }),
      makeSiegeMember({ member_id: 3, member_name: "Gamma" }),
      makeSiegeMember({ member_id: 4, member_name: "Delta" }),
    ];
    const preview = makePreview([1, 2, 3], [4]);
    registerHandlers(makeSiege(), members, preview);
    renderPage();

    const dialog = await openPreviewDialog();

    // Total visible name cells must equal exactly 4 (3 + 1), not 6 (3 + 3 padded)
    const nameCells = within(dialog).getAllByText(/^(Alpha|Beta|Gamma|Delta)$/);
    expect(nameCells).toHaveLength(4);

    // Day 2 column should NOT contain any empty cells (rendered as empty string).
    // The bug rendered 2 extra empty <div> cells in the Day 2 column.
    // Verify by checking the Day 2 column's child count equals 1 data row.
    const day2Header = within(dialog).getByText(/day 2 \(\d+\)/i);
    const day2Column = day2Header.parentElement!;
    // Children after the header div = data rows. With the bug fix: 1 row only.
    const dataRows = Array.from(day2Column.children).slice(1); // skip header child
    expect(dataRows).toHaveLength(1);
  });

  it("empty Day 2: Day 1 has entries, Day 2 is empty — zero data rows in Day 2 column", async () => {
    const members = [
      makeSiegeMember({ member_id: 1, member_name: "Alpha" }),
      makeSiegeMember({ member_id: 2, member_name: "Beta" }),
    ];
    // All assigned to Day 1, none to Day 2
    const preview = makePreview([1, 2], []);
    registerHandlers(makeSiege(), members, preview);
    renderPage();

    const dialog = await openPreviewDialog();

    // Day 1 should have 2 name cells
    const day1Names = within(dialog).getAllByText(/^(Alpha|Beta)$/);
    expect(day1Names).toHaveLength(2);

    // Day 2 column should have zero data rows
    const day2Header = within(dialog).getByText(/day 2 \(\d+\)/i);
    const day2Column = day2Header.parentElement!;
    const dataRows = Array.from(day2Column.children).slice(1);
    expect(dataRows).toHaveLength(0);
  });

  it("column headers show assignment counts (Day 1 (N) / Day 2 (N))", async () => {
    const members = [
      makeSiegeMember({ member_id: 1, member_name: "Alpha" }),
      makeSiegeMember({ member_id: 2, member_name: "Beta" }),
      makeSiegeMember({ member_id: 3, member_name: "Gamma" }),
    ];
    const preview = makePreview([1, 2], [3]);
    registerHandlers(makeSiege(), members, preview);
    renderPage();

    const dialog = await openPreviewDialog();

    expect(within(dialog).getByText(/day 1 \(2\)/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/day 2 \(1\)/i)).toBeInTheDocument();
  });
});
