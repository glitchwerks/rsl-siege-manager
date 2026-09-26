/**
 * PostsPage component tests
 *
 * Covers:
 *  - Posts list sorted by Post # (building_number) ascending
 *  - Priority changes must not reorder the list
 */

import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import { Routes, Route } from "react-router-dom";
import { server } from "../server";
import { renderWithProviders } from "../utils";
import PostsPage from "../../pages/PostsPage";
import type { Post, Siege } from "../../api/types";

// ─── Server lifecycle ──────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ─── Fixture factories ─────────────────────────────────────────────────────────

function makePost(overrides: Partial<Post> = {}): Post {
  return {
    id: 1,
    siege_id: 42,
    building_id: 10,
    building_number: 1,
    priority: 1,
    description: null,
    active_conditions: [],
    ...overrides,
  };
}

function makeSiege(overrides: Partial<Siege> = {}): Siege {
  return {
    id: 42,
    date: "2026-03-22",
    status: "planning",
    defense_scroll_count: 0,
    computed_scroll_count: 0,
    created_at: "2026-03-19T00:00:00Z",
    updated_at: "2026-03-19T00:00:00Z",
    ...overrides,
  };
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function renderPostsPage(siegeId: number = 42) {
  return renderWithProviders(
    <Routes>
      <Route path="/sieges/:id/posts" element={<PostsPage />} />
    </Routes>,
    { initialEntries: [`/sieges/${siegeId}/posts`] }
  );
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

describe("PostsPage — sort order", () => {
  it("renders posts in building_number ascending order regardless of priority", async () => {
    /**
     * The API returns posts where priority order disagrees with building_number order:
     *   Post #1 has priority=3 (highest)
     *   Post #2 has priority=1 (lowest)
     *   Post #3 has priority=2 (medium)
     *
     * Sorting by priority ascending would yield: Post 2, Post 3, Post 1
     * The UI must instead display by building_number ascending: Post 1, Post 2, Post 3
     */
    const posts: Post[] = [
      makePost({ id: 1, building_id: 10, building_number: 1, priority: 3 }),
      makePost({ id: 2, building_id: 20, building_number: 2, priority: 1 }),
      makePost({ id: 3, building_id: 30, building_number: 3, priority: 2 }),
    ];

    server.use(
      http.get("/api/sieges/42", () => HttpResponse.json(makeSiege())),
      http.get("/api/sieges/42/posts", () => HttpResponse.json(posts)),
      http.get("/api/sieges/42/board", () =>
        HttpResponse.json({ siege_id: 42, buildings: [] })
      )
    );

    renderPostsPage(42);

    await waitFor(() => {
      expect(screen.queryByText("Loading...")).not.toBeInTheDocument();
    });

    // Query all "Post N" headings rendered by PostRow.
    const postHeadings = screen
      .getAllByText(/^Post \d+$/)
      .map((el) => el.textContent);

    expect(postHeadings).toEqual(["Post 1", "Post 2", "Post 3"]);
  });
});

describe("PostsPage — locked editing", () => {
  it.each(["active", "complete"] as const)(
    "hides post edit controls when siege is %s",
    async (status) => {
      server.use(
        http.get("/api/sieges/42", () =>
          HttpResponse.json(makeSiege({ status }))
        ),
        http.get("/api/sieges/42/posts", () => HttpResponse.json([makePost()])),
        http.get("/api/sieges/42/board", () =>
          HttpResponse.json({ siege_id: 42, buildings: [] })
        )
      );

      renderPostsPage();
      await screen.findByText("Post 1");

      const postRow = screen.getByText("Post 1").closest("div.rounded-lg");
      expect(postRow?.querySelector("button")).toBeNull();
    }
  );
});
