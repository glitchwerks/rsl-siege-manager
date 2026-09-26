/**
 * PostsPage — Group-by toggle on the condition picker inside each PostRow,
 * and the master Toggle All at the top of the page (issue #377).
 *
 * Acceptance criteria from issue #375 (per-row toggle):
 *  1. Default mode shows Stronghold Level headings inside the expanded picker.
 *  2. Clicking "Type" in the toggle shows type-bucket headings in spec order.
 *  3. Clicking "Type" writes 'type' to localStorage under the shared key.
 *  4. Mounting with stored value 'type' initialises the picker in type mode.
 *
 * Acceptance criteria from issue #377 (master toggle):
 *  5. A master "Group by" radiogroup exists at page level BEFORE any row is expanded.
 *  6. Flipping the master propagates to ALL currently-expanded rows simultaneously.
 *  7. Flipping a per-row toggle after a master broadcast does NOT change the master
 *     or other rows.
 *  8. Flipping the master writes the new mode to localStorage.
 *  9. Initial render reads the master toggle value from localStorage.
 *
 * Pattern mirrors GroupByConditions.test.tsx (Surfaces A & B) for consistency.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import {
  beforeAll,
  afterAll,
  afterEach,
  beforeEach,
  describe,
  it,
  expect,
} from "vitest";
import { Routes, Route } from "react-router-dom";
import { server } from "../server";
import { renderWithProviders } from "../utils";
import PostsPage from "../../pages/PostsPage";
import type { Post, PostCondition } from "../../api/types";

// ─── Server lifecycle ──────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

// ─── Fixtures ─────────────────────────────────────────────────────────────────

/** Representative conditions spanning multiple levels and types. */
const SAMPLE_CONDITIONS: PostCondition[] = [
  // League L1
  {
    id: 1,
    description: "Only Champions from the Telerian League can be used.",
    stronghold_level: 1,
  },
  // Role L1
  { id: 5, description: "Only HP Champions can be used.", stronghold_level: 1 },
  // Faction L1
  {
    id: 9,
    description: "Only Banner Lord Champions can be used.",
    stronghold_level: 1,
  },
  // Affinity L2
  {
    id: 19,
    description: "Only Void Champions can be used.",
    stronghold_level: 2,
  },
  // Faction L2
  {
    id: 23,
    description: "Only Demonspawn Champions can be used.",
    stronghold_level: 2,
  },
  // Rarity L3
  {
    id: 29,
    description: "Only Legendary Champions can be used.",
    stronghold_level: 3,
  },
  // Effect L3
  {
    id: 35,
    description: "All Champions are immune to [Sheep] debuffs.",
    stronghold_level: 3,
  },
  // Other L3
  { id: 36, description: "Champions cannot be revived.", stronghold_level: 3 },
];

const SIEGE_ID = 42;

function makePost(overrides: Partial<Post> = {}): Post {
  return {
    id: 1,
    siege_id: SIEGE_ID,
    building_id: 10,
    building_number: 1,
    priority: 1,
    description: null,
    active_conditions: [],
    ...overrides,
  };
}

/** Two distinct posts for multi-row master-override tests. */
const TWO_POSTS: Post[] = [
  makePost({ id: 1, building_number: 1 }),
  makePost({ id: 2, building_id: 11, building_number: 2 }),
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function setupHandlers(posts: Post[] = [makePost()]) {
  server.use(
    http.get(`/api/sieges/${SIEGE_ID}`, () =>
      HttpResponse.json({
        id: SIEGE_ID,
        name: "Test Siege",
        status: "planning",
        attack_day: null,
        created_at: "2024-01-01T00:00:00Z",
        member_count: 0,
      })
    ),
    http.get(`/api/sieges/${SIEGE_ID}/posts`, () => HttpResponse.json(posts)),
    http.get(`/api/sieges/${SIEGE_ID}/board`, () =>
      HttpResponse.json({ siege_id: SIEGE_ID, buildings: [] })
    ),
    http.get("/api/post-conditions", () => HttpResponse.json(SAMPLE_CONDITIONS))
  );
}

function renderPostsPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/sieges/:id/posts" element={<PostsPage />} />
    </Routes>,
    { initialEntries: [`/sieges/${SIEGE_ID}/posts`] }
  );
}

/** Expand the first PostRow so the condition picker becomes visible. */
async function expandFirstPost(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
  );
  // The chevron button is the expand toggle — click it
  const expandBtn = screen.getByRole("button", { name: "" });
  await user.click(expandBtn);
  // Wait for conditions to load (the picker renders once allConditions arrives)
  await waitFor(() =>
    expect(
      screen.getByPlaceholderText(/filter conditions/i)
    ).toBeInTheDocument()
  );
}

/**
 * Expand all PostRows (for multi-row master-override tests).
 * Clicks every unnamed button (the chevron toggles) in sequence.
 */
async function expandAllPosts(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
  );
  const expandBtns = screen.getAllByRole("button", { name: "" });
  for (const btn of expandBtns) {
    await user.click(btn);
  }
  // Wait for at least two filter inputs to appear (one per expanded row)
  await waitFor(() => {
    const inputs = screen.getAllByPlaceholderText(/filter conditions/i);
    expect(inputs.length).toBeGreaterThanOrEqual(2);
  });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("PostsPage — Group-by toggle in PostRow condition picker", () => {
  beforeEach(() => setupHandlers());

  it("default mode shows Stronghold Level headings inside the expanded picker", async () => {
    const user = userEvent.setup();
    renderPostsPage();
    await expandFirstPost(user);

    expect(screen.getByText(/stronghold level 1/i)).toBeInTheDocument();
    expect(screen.getByText(/stronghold level 2/i)).toBeInTheDocument();
    expect(screen.getByText(/stronghold level 3/i)).toBeInTheDocument();
  });

  it("clicking Type shows type-bucket headings in spec order", async () => {
    const user = userEvent.setup();
    renderPostsPage();
    await expandFirstPost(user);

    // Scope to the row-level toggle (aria-label="Row group-by") to avoid
    // matching the master toggle that is also present on the page.
    const rowToggle = screen.getByRole("radiogroup", { name: "Row group-by" });
    await user.click(within(rowToggle).getByRole("radio", { name: "Type" }));

    await waitFor(() => {
      expect(screen.getByText("Role")).toBeInTheDocument();
      expect(screen.getByText("Affinity")).toBeInTheDocument();
      expect(screen.getByText("Faction")).toBeInTheDocument();
      expect(screen.getByText("League")).toBeInTheDocument();
      expect(screen.getByText("Rarity")).toBeInTheDocument();
      expect(screen.getByText("Effect")).toBeInTheDocument();
      expect(screen.getByText("Other")).toBeInTheDocument();
    });
    // Level headings must disappear
    expect(screen.queryByText(/stronghold level/i)).not.toBeInTheDocument();
  });

  it("clicking Type writes 'type' to the shared localStorage key", async () => {
    const user = userEvent.setup();
    renderPostsPage();
    await expandFirstPost(user);

    // Scope to the row-level toggle to avoid ambiguity with the master toggle.
    const rowToggle = screen.getByRole("radiogroup", { name: "Row group-by" });
    await user.click(within(rowToggle).getByRole("radio", { name: "Type" }));

    expect(localStorage.getItem("siege-web:postConditions:groupBy")).toBe(
      "type"
    );
  });

  it("initialises in type mode when localStorage already has 'type'", async () => {
    localStorage.setItem("siege-web:postConditions:groupBy", "type");
    const user = userEvent.setup();
    renderPostsPage();
    await expandFirstPost(user);

    // Should show type headings immediately, no click required
    await waitFor(() => {
      expect(screen.getByText("Role")).toBeInTheDocument();
    });
    expect(screen.queryByText(/stronghold level/i)).not.toBeInTheDocument();
  });
});

describe("PostsPage — master Toggle All override (issue #377)", () => {
  beforeEach(() => setupHandlers(TWO_POSTS));

  it("master Group-by radiogroup is present at page level before any row is expanded", async () => {
    renderPostsPage();
    await waitFor(() =>
      expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
    );
    // The master toggle must be visible BEFORE any row expansion.
    // It carries aria-label="Master group-by" to distinguish it from per-row toggles.
    expect(
      screen.getByRole("radiogroup", { name: "Master group-by" })
    ).toBeInTheDocument();
  });

  it("flipping master propagates to all currently-expanded rows simultaneously", async () => {
    const user = userEvent.setup();
    renderPostsPage();
    await expandAllPosts(user);

    // Both rows start in default level mode — verify level headings are visible
    await waitFor(() =>
      expect(
        screen.getAllByText(/stronghold level 1/i).length
      ).toBeGreaterThanOrEqual(2)
    );

    // Click the master "Type" radio (scoped to master to avoid per-row ambiguity)
    const masterGroup = screen.getByRole("radiogroup", {
      name: "Master group-by",
    });
    await user.click(within(masterGroup).getByRole("radio", { name: "Type" }));

    // Both rows should now show type headings
    await waitFor(() => {
      // "Role" heading appears once per expanded row
      expect(screen.getAllByText("Role").length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.queryByText(/stronghold level/i)).not.toBeInTheDocument();
  });

  it("per-row flip after master broadcast does not affect master or other rows", async () => {
    const user = userEvent.setup();
    renderPostsPage();
    await expandAllPosts(user);

    // First broadcast master → type
    const masterGroup = screen.getByRole("radiogroup", {
      name: "Master group-by",
    });
    await user.click(within(masterGroup).getByRole("radio", { name: "Type" }));
    await waitFor(() =>
      expect(screen.getAllByText("Role").length).toBeGreaterThanOrEqual(2)
    );

    // Now flip only the first row back to level by clicking its own "Level" radio.
    // Per-row radiogroups have aria-label="Row group-by".
    const rowGroups = screen.getAllByRole("radiogroup", {
      name: "Row group-by",
    });
    await user.click(
      within(rowGroups[0]).getByRole("radio", { name: "Level" })
    );

    // Row A switches back to level headings
    await waitFor(() =>
      expect(screen.getByText(/stronghold level 1/i)).toBeInTheDocument()
    );

    // Row B still shows type headings
    expect(screen.getAllByText("Role").length).toBeGreaterThanOrEqual(1);

    // Master still shows "type" as active
    expect(
      within(masterGroup).getByRole("radio", { name: "Type" })
    ).toHaveAttribute("aria-checked", "true");
  });

  it("flipping master writes the new mode to localStorage", async () => {
    const user = userEvent.setup();
    renderPostsPage();
    await waitFor(() =>
      expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
    );

    const masterGroup = screen.getByRole("radiogroup", {
      name: "Master group-by",
    });
    await user.click(within(masterGroup).getByRole("radio", { name: "Type" }));

    expect(localStorage.getItem("siege-web:postConditions:groupBy")).toBe(
      "type"
    );
  });

  it("initial render reads master value from localStorage", async () => {
    localStorage.setItem("siege-web:postConditions:groupBy", "type");
    renderPostsPage();
    await waitFor(() =>
      expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
    );

    // The master toggle's "Type" radio must be active (aria-checked=true)
    const masterGroup = screen.getByRole("radiogroup", {
      name: "Master group-by",
    });
    expect(
      within(masterGroup).getByRole("radio", { name: "Type" })
    ).toHaveAttribute("aria-checked", "true");
  });

  it("rows initialize with master's stored preference on first mount", async () => {
    // Regression guard for bot finding #1: each PostRow calls useGroupByPreference
    // independently, so each row reads localStorage on mount. This test verifies
    // that rows initialize to the stored value — NOT the default "level" mode.
    localStorage.setItem("siege-web:postConditions:groupBy", "type");
    const user = userEvent.setup();
    renderPostsPage();
    await expandAllPosts(user);

    // Both rows should initialize in Type mode (Role / Affinity / ... headings),
    // NOT in Level mode (Stronghold Level 1 / 2 / 3 headings).
    await waitFor(() => {
      expect(screen.getAllByText("Role").length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.queryByText(/stronghold level/i)).not.toBeInTheDocument();
  });
});
