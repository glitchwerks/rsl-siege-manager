import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { server } from "../server";
import SiegeLayout from "../../components/SiegeLayout";
import type { Siege } from "../../api/types";

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

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

function renderLayout(initialPath: string, siege: Siege = makeSiege()) {
  server.use(
    http.get(`/api/sieges/${siege.id}`, () => HttpResponse.json(siege))
  );

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });

  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/sieges/:id" element={<SiegeLayout />}>
            <Route index element={<div>Settings content</div>} />
            <Route path="board" element={<div>Board content</div>} />
            <Route path="posts" element={<div>Posts content</div>} />
            <Route path="members" element={<div>Members content</div>} />
            <Route path="compare" element={<div>Compare content</div>} />
          </Route>
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("SiegeLayout", () => {
  it("renders all five nav tabs", () => {
    renderLayout("/sieges/42/board");
    expect(screen.getByRole("link", { name: /board/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /posts/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /members/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /compare/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /settings/i })).toBeInTheDocument();
  });

  it("highlights the Board tab when on the board route", () => {
    renderLayout("/sieges/42/board");
    const boardLink = screen.getByRole("link", { name: /board/i });
    expect(boardLink).toHaveClass("border-violet-600");
    expect(boardLink).toHaveClass("text-violet-700");
  });

  it("highlights the Posts tab when on the posts route", () => {
    renderLayout("/sieges/42/posts");
    const postsLink = screen.getByRole("link", { name: /posts/i });
    expect(postsLink).toHaveClass("border-violet-600");
  });

  it("highlights the Settings tab when on the siege index route", () => {
    renderLayout("/sieges/42");
    const settingsLink = screen.getByRole("link", { name: /settings/i });
    expect(settingsLink).toHaveClass("border-violet-600");
  });

  it("inactive tabs have border-transparent", () => {
    renderLayout("/sieges/42/board");
    const postsLink = screen.getByRole("link", { name: /posts/i });
    expect(postsLink).toHaveClass("border-transparent");
    expect(postsLink).not.toHaveClass("border-violet-600");
  });

  it("renders the Outlet child content", () => {
    renderLayout("/sieges/42/board");
    expect(screen.getByText("Board content")).toBeInTheDocument();
  });

  it.each(["active", "complete"] as const)(
    "shows the locked banner when siege status is %s",
    async (status) => {
      renderLayout("/sieges/42/board", makeSiege({ status }));
      expect(
        await screen.findByText(/siege data cannot be edited/i)
      ).toBeInTheDocument();
    }
  );

  it("does not show locked banner for a planning siege", () => {
    renderLayout("/sieges/42/board", makeSiege({ status: "planning" }));
    expect(screen.queryByText(/this siege is locked/i)).not.toBeInTheDocument();
  });
});
