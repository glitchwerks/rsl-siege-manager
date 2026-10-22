import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import App from "../../App";
import { server } from "../server";
import { renderWithProviders } from "../utils";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const identity = (allowed: boolean) => ({
  member_id: 7,
  name: "Planner",
  role: "heavy_hitter",
  discord_id: "123",
  guides: { mom_bot: allowed },
});
const privateDocument = {
  title: "Private officer reference",
  audience: "Officers",
  intro: "Private content",
  sections: [],
};

function openGuide() {
  return renderWithProviders(<App />, { initialEntries: ["/guide/mom-bot"] });
}

describe("Private guide route", () => {
  it("sends anonymous readers to login", async () => {
    server.use(
      http.get("/api/auth/me", () => new HttpResponse(null, { status: 401 }))
    );
    openGuide();
    expect(
      await screen.findByRole("button", { name: "Sign in with Discord" })
    ).toBeInTheDocument();
    expect(screen.queryByText("Private content")).not.toBeInTheDocument();
  });

  it("requests authoritative access even when the optional identity hint is false", async () => {
    const request = vi.fn();
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json(identity(false))),
      http.get("/api/guides/mom-bot", () => {
        request();
        return HttpResponse.json(privateDocument);
      })
    );
    openGuide();
    expect(
      await screen.findByRole("heading", { name: "Private officer reference" })
    ).toBeInTheDocument();
    expect(request).toHaveBeenCalledOnce();
  });

  it("denies content when the protected endpoint rejects a false identity hint", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json(identity(false))),
      http.get("/api/guides/mom-bot", () => new HttpResponse(null, { status: 403 }))
    );
    openGuide();
    expect(
      await screen.findByRole("heading", { name: "Guide unavailable" })
    ).toBeInTheDocument();
    expect(screen.queryByText("Private content")).not.toBeInTheDocument();
  });

  it("loads officer content from the protected endpoint", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json(identity(true))),
      http.get("/api/guides/mom-bot", () => HttpResponse.json(privateDocument))
    );
    openGuide();
    expect(
      await screen.findByRole("heading", { name: "Private officer reference" })
    ).toBeInTheDocument();
  });

  it("shows no content when access is revoked after sign-in", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json(identity(true))),
      http.get(
        "/api/guides/mom-bot",
        () => new HttpResponse(null, { status: 403 })
      )
    );
    openGuide();
    expect(
      await screen.findByRole("heading", { name: "Guide unavailable" })
    ).toBeInTheDocument();
    expect(screen.queryByText("Private content")).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Read the General Usage Guide" })
    ).toHaveAttribute("href", "/guide");
  });
});
