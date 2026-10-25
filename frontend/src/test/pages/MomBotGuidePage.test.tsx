import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

function openGuide(path = "/guide/mom-bot") {
  return renderWithProviders(<App />, { initialEntries: [path] });
}

describe("Private guide route", () => {
  it.each(["/guide/mom-bot", "/guide/mom-bot/commands"])(
    "sends anonymous readers at %s to login",
    async (path) => {
      server.use(
        http.get("/api/auth/me", () => new HttpResponse(null, { status: 401 }))
      );
      openGuide(path);
      expect(
        await screen.findByRole("button", { name: "Sign in with Discord" })
      ).toBeInTheDocument();
      expect(screen.queryByText("Private content")).not.toBeInTheDocument();
    }
  );

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
  });

  it("navigates between instructions and the separate searchable command reference", async () => {
    const instructions = {
      id: "commands",
      title: "Run a command",
      summary: "Start here",
      tasks: [],
    };
    const reference = {
      id: "command-reference",
      title: "Command Reference",
      summary: "All commands",
      tasks: [
        {
          title: "Officer commands",
          tables: [
            {
              caption: "Reminders",
              columns: ["Command", "What it does"],
              rows: [["/member-notify-add", "Creates a recurring reminder"]],
            },
          ],
        },
      ],
    };
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json(identity(false))),
      http.get("/api/guides/mom-bot", () =>
        HttpResponse.json({
          ...privateDocument,
          sections: [instructions, reference],
        })
      )
    );
    openGuide();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Run a command" });
    expect(screen.queryByText("/member-notify-add")).not.toBeInTheDocument();
    const subpages = screen.getByRole("list", {
      name: "Private officer reference subpages",
    });
    await user.click(
      within(subpages).getByRole("link", { name: "mom-bot Command Reference" })
    );
    expect(
      await screen.findByRole("heading", {
        name: "mom-bot Command Reference",
        level: 1,
      })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("rowheader", { name: "/member-notify-add" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Run a command" })
    ).not.toBeInTheDocument();
    await user.type(screen.getByRole("searchbox"), "member-notify-add");
    expect(
      screen.getByRole("table", { name: "Reminders" })
    ).toBeInTheDocument();
    await user.click(
      within(
        screen.getByRole("navigation", { name: "Guide topics" })
      ).getByRole("link", { name: "Private officer reference" })
    );
    expect(
      await screen.findByRole("heading", { name: "Run a command" })
    ).toBeInTheDocument();
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });

  it("denies a direct command reference visit when the server rejects access", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json(identity(true))),
      http.get(
        "/api/guides/mom-bot",
        () => new HttpResponse(null, { status: 403 })
      )
    );
    openGuide("/guide/mom-bot/commands");
    expect(
      await screen.findByRole("heading", { name: "Guide unavailable" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "mom-bot Command Reference" })
    ).not.toBeInTheDocument();
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
