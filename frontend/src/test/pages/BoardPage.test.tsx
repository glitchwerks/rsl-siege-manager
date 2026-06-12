/**
 * BoardPage component tests
 *
 * Covers:
 *  - Loading state while board data is fetched
 *  - Summary bar slot counts (assigned / reserve / empty)
 *  - Position cell visual states rendered from board API data
 *  - Context-menu actions: Mark RESERVE, Clear, Assign member
 *  - MemberBucket search and role-filter interactions
 *  - Locked (siege complete) board — context menu button hidden, auto-fill disabled
 *  - Validation dialog: error-severity rows render with red badge and message
 *  - Validation dialog: warning-severity rows render with yellow badge and message
 *  - Validation dialog: "No issues found" shown when both arrays empty
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import { Routes, Route } from "react-router-dom";
import { server } from "../server";
import { renderWithProviders } from "../utils";
import BoardPage from "../../pages/BoardPage";
import type {
  BoardResponse,
  PositionResponse,
  SiegeMember,
  Siege,
} from "../../api/types";

// ─── Server lifecycle ──────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ─── Fixture factories ──────────────────────────────────────────────────────

function makePosition(
  overrides: Partial<PositionResponse> = {}
): PositionResponse {
  return {
    id: 1,
    position_number: 1,
    member_id: null,
    member_name: null,
    is_reserve: false,
    is_disabled: false,
    matched_condition_id: null,
    ...overrides,
  };
}

function makeBoard(positions: PositionResponse[] = []): BoardResponse {
  return {
    siege_id: 42,
    buildings: [
      {
        id: 10,
        building_type: "stronghold",
        building_number: 1,
        level: 5,
        is_broken: false,
        groups: [
          {
            id: 100,
            group_number: 1,
            slot_count: Math.max(positions.length, 1),
            positions: positions.length > 0 ? positions : [makePosition()],
          },
        ],
      },
    ],
  };
}

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

function makeSiegeMember(overrides: Partial<SiegeMember> = {}): SiegeMember {
  return {
    siege_id: 42,
    member_id: 1,
    member_name: "Aethon",
    member_role: "heavy_hitter",
    member_power_level: "gt_25m",
    member_is_active: true,
    attack_day: 1,
    has_reserve_set: false,
    attack_day_override: false,
    ...overrides,
  };
}

// Register the four routes BoardPage always queries.
// Board is GET /api/sieges/42/board, siege is GET /api/sieges/42, members is
// GET /api/sieges/42/members, and post-priorities is GET /api/post-priorities.
function setupDefaultHandlers(
  board: BoardResponse = makeBoard(),
  siege: Siege = makeSiege(),
  members: SiegeMember[] = []
) {
  server.use(
    http.get("/api/sieges/42/board", () => HttpResponse.json(board)),
    http.get("/api/sieges/42", () => HttpResponse.json(siege)),
    http.get("/api/sieges/42/members", () => HttpResponse.json(members)),
    http.get("/api/post-priorities", () => HttpResponse.json([]))
  );
}

function renderBoard(initialPath = "/sieges/42/board") {
  return renderWithProviders(
    <Routes>
      <Route path="/sieges/:id/board" element={<BoardPage />} />
    </Routes>,
    { initialEntries: [initialPath] }
  );
}

// ─── Loading state ──────────────────────────────────────────────────────────

describe("BoardPage — loading state", () => {
  it("shows a loading message while the board is being fetched", () => {
    server.use(
      http.get("/api/sieges/42/board", async () => {
        await new Promise(() => {}); // never resolves
      }),
      http.get("/api/sieges/42", () => HttpResponse.json(makeSiege())),
      http.get("/api/sieges/42/members", () => HttpResponse.json([])),
      http.get("/api/post-priorities", () => HttpResponse.json([]))
    );
    renderBoard();
    expect(screen.getByText(/loading board/i)).toBeInTheDocument();
  });
});

// ─── Summary bar ───────────────────────────────────────────────────────────

describe("BoardPage — summary bar", () => {
  it("counts each slot category correctly", async () => {
    const positions: PositionResponse[] = [
      makePosition({
        id: 1,
        position_number: 1,
        member_id: 7,
        member_name: "Aethon",
      }), // assigned
      makePosition({ id: 2, position_number: 2, is_reserve: true }), // reserve
      makePosition({ id: 3, position_number: 3 }), // empty
      makePosition({ id: 4, position_number: 4, is_disabled: true }), // disabled
    ];
    setupDefaultHandlers(makeBoard(positions), makeSiege(), [
      makeSiegeMember({ member_id: 7, member_name: "Aethon" }),
    ]);
    renderBoard();

    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // Summary bar contains "4 total"
    const summaryBar = screen.getByText("total").closest("div")!;
    expect(within(summaryBar).getByText("4")).toBeInTheDocument();
    // assigned, reserve, empty each show '1'
    const ones = within(summaryBar).getAllByText("1");
    expect(ones.length).toBeGreaterThanOrEqual(3);
  });
});

// ─── Position cell states ──────────────────────────────────────────────────

describe("BoardPage — position cell states", () => {
  it("renders an assigned member name inside a position cell", async () => {
    const positions = [
      makePosition({ id: 1, member_id: 7, member_name: "Aethon" }),
    ];
    setupDefaultHandlers(makeBoard(positions), makeSiege(), [
      makeSiegeMember({ member_id: 7, member_name: "Aethon" }),
    ]);
    renderBoard();

    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );
    // Aethon appears in both the position cell and the member bucket
    expect(screen.getAllByText("Aethon").length).toBeGreaterThanOrEqual(1);
  });

  it("renders RESERVE badge for a reserve position", async () => {
    const positions = [makePosition({ id: 1, is_reserve: true })];
    setupDefaultHandlers(makeBoard(positions));
    renderBoard();

    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );
    expect(screen.getByText("RESERVE")).toBeInTheDocument();
  });

  it("renders DISABLED text with strikethrough style for a disabled position", async () => {
    const positions = [makePosition({ id: 1, is_disabled: true })];
    setupDefaultHandlers(makeBoard(positions));
    renderBoard();

    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );
    const disabledEl = screen.getByText("DISABLED");
    expect(disabledEl).toBeInTheDocument();
    expect(disabledEl).toHaveClass("line-through");
  });

  it("renders an em-dash placeholder for an empty position", async () => {
    const positions = [makePosition({ id: 1 })];
    setupDefaultHandlers(makeBoard(positions));
    renderBoard();

    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );
    // Empty positions show the "—" character
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

// ─── Context menu actions ──────────────────────────────────────────────────

describe("BoardPage — position context menu", () => {
  it("opens the context dialog when the chevron button is clicked", async () => {
    const user = userEvent.setup();
    const positions = [makePosition({ id: 1, position_number: 3 })];
    setupDefaultHandlers(makeBoard(positions));
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // The chevron button is opacity-0 until hovered; userEvent.hover makes it visible
    const cell = screen.getByText("3.").closest("div")!;
    await user.hover(cell);

    const chevron = cell.querySelector("button");
    expect(chevron).not.toBeNull();
    await user.click(chevron!);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/position 3/i)).toBeInTheDocument();
  });

  it("calls the update endpoint with is_reserve=true when Mark RESERVE is clicked", async () => {
    const user = userEvent.setup();
    const positions = [makePosition({ id: 55, position_number: 1 })];
    setupDefaultHandlers(makeBoard(positions));

    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put("/api/sieges/42/positions/55", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          makePosition({ id: 55, position_number: 1, is_reserve: true })
        );
      })
    );

    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    const cell = screen.getByText("1.").closest("div")!;
    await user.hover(cell);
    await user.click(cell.querySelector("button")!);
    await user.click(screen.getByRole("button", { name: /mark reserve/i }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody).toMatchObject({
      is_reserve: true,
      member_id: null,
    });
  });

  it("calls the update endpoint with member_id=null when Clear is clicked", async () => {
    const user = userEvent.setup();
    const positions = [
      makePosition({
        id: 57,
        position_number: 1,
        member_id: 7,
        member_name: "Aethon",
      }),
    ];
    setupDefaultHandlers(makeBoard(positions), makeSiege(), [
      makeSiegeMember({ member_id: 7, member_name: "Aethon" }),
    ]);

    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put("/api/sieges/42/positions/57", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makePosition({ id: 57, position_number: 1 }));
      })
    );

    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    const cell = screen.getByText("1.").closest("div")!;
    await user.hover(cell);
    await user.click(cell.querySelector("button")!);
    await user.click(screen.getByRole("button", { name: /^clear$/i }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody).toMatchObject({
      member_id: null,
      is_reserve: false,
    });
  });

  it("does not render a chevron button on a disabled position", async () => {
    const positions = [
      makePosition({ id: 1, position_number: 1, is_disabled: true }),
    ];
    setupDefaultHandlers(makeBoard(positions));
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // The DISABLED text sits inside the cell; no button should be present
    const disabledSpan = screen.getByText("DISABLED");
    const cell = disabledSpan.closest('[class*="group"]');
    expect(cell?.querySelector("button")).toBeNull();
  });
});

// ─── Locked board (siege complete) ────────────────────────────────────────

describe("BoardPage — locked board", () => {
  it("hides the chevron context button on all positions when siege is complete", async () => {
    const positions = [makePosition({ id: 1, position_number: 1 })];
    setupDefaultHandlers(
      makeBoard(positions),
      makeSiege({ status: "complete" })
    );
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // When isLocked=true the button is not rendered at all (conditional render, not just hidden)
    const cell = screen.getByText("1.").closest('[class*="group"]');
    expect(cell?.querySelector("button")).toBeNull();
  });

  it("disables the Preview Auto-fill button when siege is complete", async () => {
    setupDefaultHandlers(makeBoard(), makeSiege({ status: "complete" }));
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    expect(
      screen.getByRole("button", { name: /preview auto-fill/i })
    ).toBeDisabled();
  });
});

// ─── MemberBucket ─────────────────────────────────────────────────────────

describe("BoardPage — MemberBucket", () => {
  const members: SiegeMember[] = [
    makeSiegeMember({
      member_id: 1,
      member_name: "Aethon",
      member_role: "heavy_hitter",
    }),
    makeSiegeMember({
      member_id: 2,
      member_name: "Brint",
      member_role: "novice",
    }),
    makeSiegeMember({
      member_id: 3,
      member_name: "Calyx",
      member_role: "advanced",
    }),
  ];

  it("renders all siege members in the bucket", async () => {
    setupDefaultHandlers(makeBoard(), makeSiege(), members);
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    expect(screen.getByText("Aethon")).toBeInTheDocument();
    expect(screen.getByText("Brint")).toBeInTheDocument();
    expect(screen.getByText("Calyx")).toBeInTheDocument();
  });

  it("filters members by search text", async () => {
    const user = userEvent.setup();
    setupDefaultHandlers(makeBoard(), makeSiege(), members);
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    const searchInput = screen.getByPlaceholderText(/search/i);
    await user.type(searchInput, "Aeth");

    expect(screen.getByText("Aethon")).toBeInTheDocument();
    expect(screen.queryByText("Brint")).not.toBeInTheDocument();
    expect(screen.queryByText("Calyx")).not.toBeInTheDocument();
  });

  it('shows "No members" when search matches nothing', async () => {
    const user = userEvent.setup();
    setupDefaultHandlers(makeBoard(), makeSiege(), members);
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    const searchInput = screen.getByPlaceholderText(/search/i);
    await user.type(searchInput, "zzz");

    expect(screen.getByText("No members")).toBeInTheDocument();
  });

  it("filters members by role via the role select", async () => {
    const user = userEvent.setup();
    setupDefaultHandlers(makeBoard(), makeSiege(), members);
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // MemberBucket uses a plain <select>, not a shadcn Select component
    const roleSelect = screen
      .getAllByRole("combobox")
      .find((el) => el.tagName === "SELECT")!;
    await user.selectOptions(roleSelect, "novice");

    expect(screen.getByText("Brint")).toBeInTheDocument();
    expect(screen.queryByText("Aethon")).not.toBeInTheDocument();
    expect(screen.queryByText("Calyx")).not.toBeInTheDocument();
  });

  it("shows role abbreviation badge next to each member", async () => {
    setupDefaultHandlers(makeBoard(), makeSiege(), [
      makeSiegeMember({
        member_id: 1,
        member_name: "Aethon",
        member_role: "heavy_hitter",
      }),
    ]);
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // heavy_hitter → "HH" abbreviation badge
    expect(screen.getByText("HH")).toBeInTheDocument();
  });

  it("shows assignment count badge for each member", async () => {
    // Aethon is assigned to two positions
    const positions = [
      makePosition({
        id: 1,
        position_number: 1,
        member_id: 1,
        member_name: "Aethon",
      }),
      makePosition({
        id: 2,
        position_number: 2,
        member_id: 1,
        member_name: "Aethon",
      }),
    ];
    setupDefaultHandlers(makeBoard(positions), makeSiege(), [
      makeSiegeMember({
        member_id: 1,
        member_name: "Aethon",
        member_role: "heavy_hitter",
      }),
    ]);
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // The count badge "2" should appear in the member bucket next to Aethon
    // Note: the MemberBucket is in the same DOM tree as the board, so scope to Aethon's row
    const memberRows = screen.getAllByText("Aethon");
    // First match is the bucket row; find the "2" count badge inside its parent
    const bucketRow = memberRows[0].closest(
      'div[class*="flex"]'
    ) as HTMLElement;
    expect(within(bucketRow).getByText("2")).toBeInTheDocument();
  });
});

// ─── Action buttons ────────────────────────────────────────────────────────

describe("BoardPage — action buttons", () => {
  it("renders Validate and Preview Auto-fill buttons", async () => {
    setupDefaultHandlers();
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    expect(
      screen.getByRole("button", { name: /validate/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /preview auto-fill/i })
    ).toBeInTheDocument();
  });

  it("renders Buildings and Posts tab buttons", async () => {
    setupDefaultHandlers();
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    expect(
      screen.getByRole("button", { name: /buildings/i })
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /posts/i })).toBeInTheDocument();
  });

  it("shows the Buildings tab content by default (Stronghold section header visible)", async () => {
    const positions = [makePosition({ id: 1, position_number: 1 })];
    setupDefaultHandlers(makeBoard(positions));
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // BuildingTypeSection renders the type label via BUILDING_LABELS; CSS uppercase doesn't change DOM text
    expect(screen.getByText("Stronghold")).toBeInTheDocument();
  });
});

// ─── Validation dialog ────────────────────────────────────────────────────

describe("BoardPage — validation dialog", () => {
  it("renders error-severity rows with destructive badge when validate returns errors", async () => {
    const user = userEvent.setup();
    setupDefaultHandlers();
    server.use(
      http.post("/api/sieges/42/validate", () =>
        HttpResponse.json({
          errors: [
            {
              rule: 1,
              message: "Assigned member 'Alice' is not active",
              context: null,
            },
          ],
          warnings: [],
        })
      )
    );
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /^validate$/i }));

    await waitFor(() =>
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    );
    expect(screen.getByText(/error 1/i)).toBeInTheDocument();
    expect(
      screen.getByText(/assigned member 'alice' is not active/i)
    ).toBeInTheDocument();
  });

  it("renders warning-severity rows with yellow badge when validate returns warnings", async () => {
    const user = userEvent.setup();
    setupDefaultHandlers();
    server.use(
      http.post("/api/sieges/42/validate", () =>
        HttpResponse.json({
          errors: [],
          warnings: [
            {
              rule: 10,
              message: "Building has fewer members than recommended",
              context: null,
            },
          ],
        })
      )
    );
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /^validate$/i }));

    await waitFor(() =>
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    );
    expect(screen.getByText(/warning 10/i)).toBeInTheDocument();
    expect(
      screen.getByText(/building has fewer members than recommended/i)
    ).toBeInTheDocument();
  });

  it("shows 'No issues found' when validate returns empty errors and warnings", async () => {
    const user = userEvent.setup();
    setupDefaultHandlers();
    server.use(
      http.post("/api/sieges/42/validate", () =>
        HttpResponse.json({ errors: [], warnings: [] })
      )
    );
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /^validate$/i }));

    await waitFor(() =>
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    );
    expect(screen.getByText(/no issues found/i)).toBeInTheDocument();
  });
});

// ─── Building section collapse ─────────────────────────────────────────────

describe("BoardPage — building section collapse/expand", () => {
  it("collapses a building section when its header is clicked", async () => {
    const user = userEvent.setup();
    const positions = [makePosition({ id: 1, position_number: 1 })];
    setupDefaultHandlers(makeBoard(positions));
    renderBoard();
    await waitFor(() =>
      expect(screen.queryByText(/loading board/i)).not.toBeInTheDocument()
    );

    // Position cell placeholder is visible in the expanded (default) state
    expect(screen.getByText("—")).toBeInTheDocument();

    // Click the section header to collapse
    const sectionHeader = screen
      .getByText("Stronghold")
      .closest('div[class*="cursor-pointer"]')!;
    await user.click(sectionHeader);

    // After collapse the position placeholder should not be rendered
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });
});
