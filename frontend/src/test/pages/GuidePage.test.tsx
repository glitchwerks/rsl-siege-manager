import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App";

afterEach(() => vi.restoreAllMocks());

describe("Public clan guide", () => {
  it("opens directly without an auth provider or clan-data requests", () => {
    // Private routes need an AuthProvider. The guide must work independently,
    // including when the reader is signed out or the sign-in service is down.
    render(
      <MemoryRouter initialEntries={["/guide"]}>
        <App />
      </MemoryRouter>
    );

    expect(
      screen.getByRole("heading", { name: "Your clan guide" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Use mom-bot in Discord" })
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open website" })).toHaveAttribute(
      "href",
      "/sieges"
    );

    const topics = within(
      screen.getByRole("navigation", { name: "Guide topics" })
    ).getAllByRole("link");
    for (const link of topics) {
      expect(
        document.getElementById(link.getAttribute("href")!.slice(1))
      ).not.toBeNull();
    }
  });

  it("scrolls a shared Discord-section link into view after rendering", () => {
    const scroll = vi.fn();
    vi.spyOn(document, "getElementById").mockReturnValue({
      scrollIntoView: scroll,
    } as unknown as HTMLElement);

    render(
      <MemoryRouter initialEntries={["/guide#mom-bot"]}>
        <App />
      </MemoryRouter>
    );

    expect(document.getElementById).toHaveBeenCalledWith("mom-bot");
    expect(scroll).toHaveBeenCalledOnce();
  });
});
