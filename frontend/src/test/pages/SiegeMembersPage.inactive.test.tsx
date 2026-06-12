/**
 * SiegeMembersPage — inactive-member indicator icon tests (issue #487)
 *
 * Verifies that:
 *  - When member_is_active is false, a UserX icon with the accessible label
 *    "Inactive member" appears next to the member's name in the row.
 *  - When member_is_active is true, no such icon is rendered.
 *  - The member name text is muted (slate-500/600 class) when the member is
 *    inactive.
 */

import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import { Routes, Route } from "react-router-dom";
import { server } from "../server";
import { renderWithProviders } from "../utils";
import SiegeMembersPage from "../../pages/SiegeMembersPage";
import type { Siege, SiegeMember } from "../../api/types";

// ─── Server lifecycle ──────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ─── Fixture factories ────────────────────────────────────────────────────────

function makeSiege(overrides: Partial<Siege> = {}): Siege {
  return {
    id: 42,
    date: "2026-06-12",
    status: "active",
    defense_scroll_count: 0,
    computed_scroll_count: 0,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    ...overrides,
  };
}

function makeSiegeMember(overrides: Partial<SiegeMember> = {}): SiegeMember {
  return {
    siege_id: 42,
    member_id: 1,
    member_name: "Alice",
    member_role: "advanced",
    member_power_level: null,
    member_is_active: true,
    attack_day: 1,
    has_reserve_set: false,
    attack_day_override: false,
    ...overrides,
  };
}

function registerHandlers(siege: Siege, members: SiegeMember[]) {
  server.use(
    http.get(`/api/sieges/${siege.id}`, () => HttpResponse.json(siege)),
    http.get(`/api/sieges/${siege.id}/members`, () =>
      HttpResponse.json(members)
    )
  );
}

function renderPage(siegeId = 42) {
  return renderWithProviders(
    <Routes>
      <Route path="/sieges/:id/members" element={<SiegeMembersPage />} />
    </Routes>,
    { initialEntries: [`/sieges/${siegeId}/members`] }
  );
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("SiegeMembersPage — inactive member indicator icon (#487)", () => {
  it("renders an accessible inactive-member icon when member_is_active is false", async () => {
    const siege = makeSiege({ status: "active" });
    const members = [makeSiegeMember({ member_is_active: false })];
    registerHandlers(siege, members);
    renderPage();

    // Wait for the member row to appear
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    // The inactive icon must have the accessible label "Inactive member"
    expect(
      screen.getByLabelText("Inactive member")
    ).toBeInTheDocument();
  });

  it("does not render an inactive-member icon when member_is_active is true", async () => {
    const siege = makeSiege({ status: "active" });
    const members = [makeSiegeMember({ member_is_active: true })];
    registerHandlers(siege, members);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    // No inactive icon expected
    expect(
      screen.queryByLabelText("Inactive member")
    ).not.toBeInTheDocument();
  });

  it("mutes the name text when the member is inactive", async () => {
    const siege = makeSiege({ status: "active" });
    const members = [makeSiegeMember({ member_is_active: false })];
    registerHandlers(siege, members);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    // The name element must carry a muted-text class (slate-500 or slate-600)
    const nameEl = screen.getByText("Alice");
    expect(
      nameEl.className.includes("text-slate-500") ||
        nameEl.className.includes("text-slate-600")
    ).toBe(true);
  });
});
