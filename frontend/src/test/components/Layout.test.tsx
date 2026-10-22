import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../utils";
import { server } from "../server";
import Layout from "../../components/Layout";

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderLayout() {
  return renderWithProviders(
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<div>page content</div>} />
      </Route>
    </Routes>,
    { initialEntries: ["/"] }
  );
}

describe("Layout demo banner", () => {
  it("shows the demo banner when auth_disabled is true", async () => {
    server.use(
      http.get("/api/config", () => HttpResponse.json({ auth_disabled: true }))
    );

    renderLayout();

    await waitFor(() => {
      expect(screen.getByText(/demo mode/i)).toBeInTheDocument();
    });
  });

  it("does not show the demo banner when auth_disabled is false", async () => {
    server.use(
      http.get("/api/config", () => HttpResponse.json({ auth_disabled: false }))
    );

    renderLayout();

    // Allow queries to settle then assert banner is absent.
    await waitFor(() => {
      expect(screen.queryByText(/demo mode/i)).not.toBeInTheDocument();
    });
  });

  it("does not show the demo banner when /api/config returns an error (fail-closed)", async () => {
    server.use(http.get("/api/config", () => HttpResponse.error()));

    renderLayout();

    // Give React Query time to attempt and fail the fetch.
    await waitFor(() => {
      expect(screen.queryByText(/demo mode/i)).not.toBeInTheDocument();
    });
  });
});

describe("Private guide navigation", () => {
  it("does not advertise guild-specific help without access", async () => {
    renderLayout();
    await screen.findByText("TestUser");
    expect(
      screen.queryByRole("link", { name: "mom-bot Guide" })
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Guide" })).toHaveAttribute(
      "href",
      "/guide"
    );
  });

  it("shows the private guide only with the server-granted capability", async () => {
    server.use(
      http.get("/api/auth/me", () =>
        HttpResponse.json({
          member_id: 7,
          name: "Officer",
          role: "novice",
          discord_id: "123",
          guides: { mom_bot: true },
        })
      )
    );
    renderLayout();
    expect(
      await screen.findByRole("link", { name: "mom-bot Guide" })
    ).toHaveAttribute("href", "/guide/mom-bot");
  });
});
