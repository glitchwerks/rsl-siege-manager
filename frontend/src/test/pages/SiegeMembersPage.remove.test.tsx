/**
 * SiegeMembersPage — remove-member tests (issue #486)
 *
 * Verifies that:
 *  - A trash (remove) button is visible per member row when siege is planning.
 *  - The remove button is NOT visible when the siege is not planning.
 *  - Clicking the remove button opens a confirmation dialog.
 *  - Confirming calls DELETE /api/sieges/:id/members/:memberId.
 *  - Cancelling closes the dialog without calling the API.
 */

import { screen, waitFor, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, describe, it, expect, vi } from "vitest";
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
    status: "planning",
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
    attack_day: null,
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

describe("SiegeMembersPage — remove member (#486)", () => {
  it("shows a remove button per member row when siege is planning", async () => {
    const siege = makeSiege({ status: "planning" });
    const members = [makeSiegeMember()];
    registerHandlers(siege, members);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    // Trash icon button should be visible (aria-label "Remove Alice from siege")
    expect(
      screen.getByLabelText("Remove Alice from siege")
    ).toBeInTheDocument();
  });

  it("does not show a remove button when siege is active", async () => {
    const siege = makeSiege({ status: "active" });
    const members = [makeSiegeMember()];
    registerHandlers(siege, members);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    expect(
      screen.queryByLabelText("Remove Alice from siege")
    ).not.toBeInTheDocument();
  });

  it("does not show a remove button when siege is complete", async () => {
    const siege = makeSiege({ status: "complete" });
    const members = [makeSiegeMember()];
    registerHandlers(siege, members);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    expect(
      screen.queryByLabelText("Remove Alice from siege")
    ).not.toBeInTheDocument();
  });

  it("opens a confirmation dialog when the remove button is clicked", async () => {
    const siege = makeSiege({ status: "planning" });
    const members = [makeSiegeMember()];
    registerHandlers(siege, members);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText("Remove Alice from siege"));

    await waitFor(() => {
      expect(
        screen.getByText("Remove member from siege?")
      ).toBeInTheDocument();
    });
  });

  it("calls DELETE endpoint when removal is confirmed", async () => {
    const siege = makeSiege({ status: "planning" });
    const member = makeSiegeMember({ member_id: 7, member_name: "Bob" });

    // Register initial handlers: siege loads, members list shows Bob initially.
    registerHandlers(siege, [member]);

    const deleteSpy = vi.fn();

    // Override: DELETE handler returns 204, and the members refetch returns [].
    // Use once() so the first GET returns [member] and the second returns [].
    server.use(
      http.delete(
        `/api/sieges/${siege.id}/members/${member.member_id}`,
        () => {
          deleteSpy();
          return new HttpResponse(null, { status: 204 });
        }
      )
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Bob")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText("Remove Bob from siege"));

    await waitFor(() => {
      expect(screen.getByText("Remove member from siege?")).toBeInTheDocument();
    });

    // Click the confirm "Remove" button inside the dialog
    const confirmButton = screen.getByRole("button", { name: "Remove" });
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledOnce();
    });
  });

  it("closes the dialog without calling the API when cancel is clicked", async () => {
    const siege = makeSiege({ status: "planning" });
    const members = [makeSiegeMember()];
    registerHandlers(siege, members);

    const deleteSpy = vi.fn();
    server.use(
      http.delete(`/api/sieges/${siege.id}/members/1`, () => {
        deleteSpy();
        return new HttpResponse(null, { status: 204 });
      })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText("Remove Alice from siege"));

    await waitFor(() => {
      expect(screen.getByText("Remove member from siege?")).toBeInTheDocument();
    });

    // Click Cancel
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(
        screen.queryByText("Remove member from siege?")
      ).not.toBeInTheDocument();
    });

    expect(deleteSpy).not.toHaveBeenCalled();
  });
});
