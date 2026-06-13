import { screen, waitFor } from "@testing-library/react";
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
import { renderWithProviders } from "../utils";
import { server } from "../server";
import ChangelogDropdown from "../../components/ChangelogDropdown";

// ---------------------------------------------------------------------------
// virtual:changelog mock — inline-markdown test fixture
// ---------------------------------------------------------------------------
// The bullet below exercises bold, italic, inline-code, markdown link, and
// bare GitHub ref all in one real-world-shaped string.
// A second bullet carries a raw-HTML payload for the XSS assertion.
vi.mock("virtual:changelog", () => ({
  changelog: [
    {
      version: "2.0.0",
      releaseDate: "2026-06-01",
      sections: {
        Added: [
          "**Suggest Post Assignments** preview via `AUTH_LOGIN_RATE_LIMIT` — see [docs](https://example.com) (#335, #348)",
          "*italic label* for new feature flag",
          '<img src=x onerror="window.__XSS_FIRED__=true"> raw HTML injection attempt',
        ],
      },
    },
  ],
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderDropdown() {
  return renderWithProviders(<ChangelogDropdown />);
}

/** Open the dropdown and return after the content is mounted. */
async function openDropdown(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(screen.getByTestId("changelog-button")).toBeInTheDocument()
  );
  await user.click(screen.getByTestId("changelog-button"));
  // Wait for at least one version heading to confirm the panel is open.
  await screen.findByText(/2\.0\.0/);
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ---------------------------------------------------------------------------
// Suite — inline-markdown rendering
// ---------------------------------------------------------------------------

describe("ChangelogDropdown — inline-markdown rendering (AC #388)", () => {
  // Provide a stable network context: null last_seen so the dropdown opens
  // without the status check blocking the button render.
  beforeAll(() => {
    server.use(
      http.get("/api/changelog/status", () =>
        HttpResponse.json({ last_seen_changelog_at: null })
      ),
      http.post("/api/changelog/mark-seen", () =>
        HttpResponse.json({ last_seen_changelog_at: new Date().toISOString() })
      )
    );
  });

  it("renders **bold** text inside a <strong> element", async () => {
    const user = userEvent.setup();
    renderDropdown();
    await openDropdown(user);

    // <strong> has no ARIA role — query via DOM selector.
    const strong = document.querySelector("strong");
    expect(strong).toBeInTheDocument();
    expect(strong!.textContent).toBe("Suggest Post Assignments");
  });

  it("renders *italic* text inside an <em> element", async () => {
    const user = userEvent.setup();
    renderDropdown();
    await openDropdown(user);

    // <em> has no ARIA role — query via DOM selector.
    const em = document.querySelector("em");
    expect(em).toBeInTheDocument();
    expect(em!.textContent).toBe("italic label");
  });

  it("renders `inline code` inside a <code> element with the literal token text", async () => {
    const user = userEvent.setup();
    renderDropdown();
    await openDropdown(user);

    // <code> has no ARIA role — query via DOM selector.
    const code = document.querySelector("code");
    expect(code).toBeInTheDocument();
    expect(code!.textContent).toBe("AUTH_LOGIN_RATE_LIMIT");
  });

  it("renders [text](url) as an <a> with correct href, target=_blank, and rel=noopener noreferrer", async () => {
    const user = userEvent.setup();
    renderDropdown();
    await openDropdown(user);

    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    // rel must include both tokens (order-independent).
    const rel = link.getAttribute("rel") ?? "";
    expect(rel).toContain("noopener");
    expect(rel).toContain("noreferrer");
  });

  it("renders bare #335 as an <a> linking to the correct GitHub issue URL", async () => {
    const user = userEvent.setup();
    renderDropdown();
    await openDropdown(user);

    // There may be multiple issue links (#335, #348) — query all and filter.
    const links = screen.getAllByRole("link");
    const issueLink = links.find((l) => l.textContent === "#335");

    expect(issueLink).toBeDefined();
    expect(issueLink).toHaveAttribute(
      "href",
      "https://github.com/glitchwerks/rsl-siege-manager/issues/335"
    );
    expect(issueLink!.textContent).toBe("#335");
  });

  it("does not introduce block elements inside a bullet <li>", async () => {
    const user = userEvent.setup();
    renderDropdown();
    await openDropdown(user);

    // Grab all rendered <li> elements in the changelog panel.
    const listItems = document.querySelectorAll("li");
    expect(listItems.length).toBeGreaterThan(0);

    const blockSelectors = [
      "p",
      "ul",
      "ol",
      "h1",
      "h2",
      "h3",
      "h4",
      "h5",
      "h6",
      "blockquote",
      "pre",
      "img",
    ];

    for (const li of listItems) {
      for (const selector of blockSelectors) {
        // Use plain DOM queries — block elements like <p>, <strong>, <pre>,
        // <h1>-<h6>, <blockquote>, <img> have no reliable ARIA roles in
        // testing-library. querySelectorAll is the authoritative check.
        expect(
          li.querySelectorAll(selector).length,
          `Found block element <${selector}> inside a bullet <li>`
        ).toBe(0);
      }
    }
  });

  it("does NOT render a live <img> element when a bullet contains raw HTML (XSS defense)", async () => {
    const user = userEvent.setup();
    // Initialise the sentinel so we can detect if the onerror fired.
    (window as unknown as Record<string, unknown>)["__XSS_FIRED__"] = false;

    renderDropdown();
    await openDropdown(user);

    // No <img> element must appear from the injected bullet.
    // (The fixture changelog has zero legitimate images, so any <img> is a
    // sign of unsafe HTML rendering.)
    const images = document.querySelectorAll("img");
    expect(images.length).toBe(0);

    // Belt-and-suspenders: the onerror sentinel must not have fired.
    expect(
      (window as unknown as Record<string, unknown>)["__XSS_FIRED__"]
    ).toBe(false);
  });
});
