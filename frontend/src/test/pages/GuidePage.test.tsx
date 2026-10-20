import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App";

afterEach(() => vi.restoreAllMocks());

function renderGuide(entry = "/guide") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <App />
    </MemoryRouter>
  );
}

describe("Public General Usage Guide", () => {
  it("opens without auth or clan-data providers and excludes guild-specific instructions", () => {
    renderGuide();
    expect(
      screen.getByRole("heading", { name: "General Usage Guide" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /13\. Compare/ })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /11\. Assign posts/ })
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open website" })).toHaveAttribute(
      "href",
      "/sieges"
    );
    expect(
      screen.queryByText(
        /mom-bot|Magicka|member-notify|notify-new-members|post-conditions-set/i
      )
    ).not.toBeInTheDocument();
    const topics = within(
      screen.getByRole("navigation", { name: "Guide topics" })
    ).getAllByRole("link");
    expect(topics).toHaveLength(16);
    for (const link of topics)
      expect(
        document.getElementById(link.getAttribute("href")!.slice(1))
      ).not.toBeNull();
  });

  it("scrolls a shared feature link into view after rendering", () => {
    const scroll = vi.fn();
    vi.spyOn(document, "getElementById").mockReturnValue({
      scrollIntoView: scroll,
    } as unknown as HTMLElement);
    renderGuide("/guide#post-assignments");
    expect(document.getElementById).toHaveBeenCalledWith("post-assignments");
    expect(scroll).toHaveBeenCalledOnce();
  });

  it("searches detailed instructions and can restore the full guide", async () => {
    renderGuide();
    const user = userEvent.setup();
    await user.type(
      screen.getByRole("searchbox", { name: "Find help" }),
      "Apply remaining"
    );
    expect(
      screen.getByRole("heading", { name: /11\. Assign posts/ })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /3\. Manage the clan roster/ })
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Show the full guide" })
    );
    expect(
      screen.getByRole("heading", { name: /3\. Manage the clan roster/ })
    ).toBeInTheDocument();
  });

  it("explains an unmatched search", async () => {
    renderGuide();
    await userEvent
      .setup()
      .type(screen.getByRole("searchbox", { name: "Find help" }), "zzzzzzzz");
    expect(screen.getByText(/No topics match/)).toBeInTheDocument();
  });
});
