import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import { guidePages } from "../../guides/pages";

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function renderGuide(entry = "/guide") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <App />
    </MemoryRouter>
  );
}
function topics() {
  return within(screen.getByRole("navigation", { name: "Guide topics" }));
}

describe("Public General Usage Guide", () => {
  it("opens Quick Start publicly with four pages and no private instructions", () => {
    renderGuide();
    expect(
      screen.getByRole("heading", { name: "Quick Start" })
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("navigation", { name: "Guide topics" })
    ).toHaveLength(1);
    expect(topics().getByRole("link", { name: "Quick Start" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    for (const page of guidePages)
      expect(topics().getByRole("link", { name: page.title })).toHaveAttribute(
        "href",
        page.href
      );
    expect(screen.getByRole("link", { name: "Open website" })).toHaveAttribute(
      "href",
      "/sieges"
    );
    expect(
      screen.queryByRole("heading", { name: "Manage the clan roster" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        /mom-bot|Magicka|member-notify|notify-new-members|post-conditions-set/i
      )
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Know when a change is saved")
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Top navigation bar/ })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Siege navigation bar/ })
    ).toBeInTheDocument();
  });

  it.each(guidePages)(
    "loads $title directly with only its own subsections",
    (page) => {
      renderGuide(page.href);
      expect(
        screen.getByRole("heading", { name: page.title, level: 1 })
      ).toBeInTheDocument();
      expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(
        page.sections.length
      );
      for (const section of page.sections) {
        expect(
          screen.getByRole("heading", { name: section.title, level: 2 })
        ).toBeInTheDocument();
        expect(
          topics().getByRole("link", { name: section.title })
        ).toHaveAttribute("href", `${page.href}#${section.id}`);
      }
    }
  );

  it("moves between pages, clears page searches, and keeps shared navigation", async () => {
    renderGuide();
    const user = userEvent.setup();
    await user.type(screen.getByRole("searchbox"), "zzzzzz");
    await user.click(topics().getByRole("link", { name: "Clan Management" }));
    expect(
      screen.getByRole("heading", { name: "Clan Management", level: 1 })
    ).toBeInTheDocument();
    expect(screen.getByRole("searchbox")).toHaveValue("");
    expect(
      screen.queryByRole("heading", { name: "Sign in & get started" })
    ).not.toBeInTheDocument();
    await user.click(topics().getByRole("link", { name: "Siege Management" }));
    expect(
      screen.getByRole("heading", { name: "Assign posts manually" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Set shared post priorities" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", {
        name: "Help a member who did not receive a DM",
      })
    ).not.toBeInTheDocument();
    await user.click(
      topics().getByRole("link", { name: "FAQ and Troubleshooting" })
    );
    expect(
      screen.getByRole("heading", {
        name: "Help a member who did not receive a DM",
      })
    ).toBeInTheDocument();
  });

  it.each([
    ["/guide#post-assignments", "Assign posts manually"],
    ["/guide/clan-management#board", "Build & edit the assignment board"],
  ])("opens the correct page for an older topic link: %s", (entry, title) => {
    renderGuide(entry);
    expect(
      screen.getByRole("heading", { name: "Siege Management", level: 1 })
    ).toBeInTheDocument();
    expect(topics().getByRole("link", { name: title })).toHaveAttribute(
      "aria-current",
      "location"
    );
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("searches within a page and restores its topics", async () => {
    renderGuide("/guide/siege-management");
    const user = userEvent.setup();
    await user.type(screen.getByRole("searchbox"), "Apply remaining");
    expect(
      screen.getByRole("heading", { name: "Automatic post assignments" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", {
        name: "Build & edit the assignment board",
      })
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Show all topics on this page" })
    );
    expect(
      screen.getByRole("heading", { name: "Build & edit the assignment board" })
    ).toBeInTheDocument();
  });

  it("minimizes and expands the one shared bar and remembers the preference", async () => {
    const user = userEvent.setup();
    const view = renderGuide();
    await user.click(
      screen.getByRole("button", { name: "Minimize guide navigation" })
    );
    expect(
      screen.queryByRole("navigation", { name: "Guide topics" })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Expand guide navigation" })
    ).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getByRole("heading", { name: "Quick Start", level: 1 })
    ).toBeInTheDocument();
    view.unmount();
    renderGuide("/guide/clan-management");
    expect(
      screen.getByRole("button", { name: "Expand guide navigation" })
    ).toHaveAttribute("aria-expanded", "false");
    await user.click(
      screen.getByRole("button", { name: "Expand guide navigation" })
    );
    expect(
      screen.getAllByRole("navigation", { name: "Guide topics" })
    ).toHaveLength(1);
  });

  it("lets phone readers expand the shared bar and closes it after choosing a subsection", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({ matches: query.includes("max-width") }))
    );
    renderGuide("/guide/siege-management");
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Expand guide navigation" })
    );
    await user.click(
      topics().getByRole("link", { name: "Build & edit the assignment board" })
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Expand guide navigation" })
      ).toHaveAttribute("aria-expanded", "false")
    );
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("opens screenshots with full context and closes them with Escape", async () => {
    renderGuide("/guide/clan-management");
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", {
        name: "Enlarge screenshot: Choose and save preferences",
      })
    );
    expect(within(screen.getByRole("dialog")).getByRole("img")).toHaveAttribute(
      "src",
      "/guide-images/preferences.jpg"
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("updates the current subsection when scrolling", async () => {
    renderGuide("/guide/siege-management");
    for (const section of document.querySelectorAll("section[id]"))
      vi.spyOn(section, "getBoundingClientRect").mockReturnValue({
        top: section.id === "board" ? 100 : 1000,
      } as DOMRect);
    fireEvent.scroll(window);
    await waitFor(() =>
      expect(
        topics().getByRole("link", {
          name: "Build & edit the assignment board",
        })
      ).toHaveAttribute("aria-current", "location")
    );
  });

  it("shows building capacity by group and level at the chart link", () => {
    renderGuide("/guide/siege-management#building-slots");
    expect(document.getElementById("building-slots")).not.toBeNull();
    expect(
      topics().getByRole("link", { name: "Set the date & building layout" })
    ).toHaveAttribute("aria-current", "location");
    expect(
      within(document.getElementById("building-slots")!).getAllByRole("table")
    ).toHaveLength(4);
    const shrine = within(
      screen.getByRole("table", { name: "Mana Shrine — slots per group" })
    );
    const levelTwo = shrine
      .getByRole("rowheader", { name: "2" })
      .closest("tr")!;
    expect(
      within(levelTwo)
        .getAllByRole("cell")
        .map((cell) => cell.textContent)
    ).toEqual(["3", "3", "1", "—", "—", "7"]);
    expect(
      screen.getByRole("link", { name: "building level slot chart" })
    ).toHaveAttribute("href", "#building-slots");
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("separates manual post assignment from automatic suggestions in that order", () => {
    renderGuide("/guide/siege-management#post-suggestions");
    const manual = screen.getByRole("region", {
      name: "Assign posts manually",
    });
    const automatic = screen.getByRole("region", {
      name: "Automatic post assignments",
    });
    expect(
      manual.compareDocumentPosition(automatic) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    expect(
      within(manual).getByRole("heading", { name: "Assign one post manually" })
    ).toBeInTheDocument();
    expect(within(manual).queryByRole("table")).not.toBeInTheDocument();
    const warning = within(manual).getByRole("complementary", {
      name: "Duplicate condition warning",
    });
    expect(
      within(warning).getByRole("button", {
        name: "Enlarge screenshot: Review a duplicate condition before confirming",
      })
    ).toBeInTheDocument();
    expect(
      within(automatic).getByRole("table", {
        name: "Automatic post-assignment decision chart",
      })
    ).toBeInTheDocument();
    expect(
      topics().getByRole("link", { name: "Automatic post assignments" })
    ).toHaveAttribute("aria-current", "location");
  });

  it("recovers unknown page addresses to Quick Start", () => {
    renderGuide("/guide/missing");
    expect(
      screen.getByRole("heading", { name: "Quick Start", level: 1 })
    ).toBeInTheDocument();
  });
});
