import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { isAxiosError } from "axios";
import { useAuth } from "../context/AuthContext";
import apiClient from "../api/client";
import GuideDocument from "../components/GuideDocument";
import type { GuideDocument as Document } from "../guides/types";
import { guidePages } from "../guides/pages";

export default function MomBotGuidePage() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  // The identity capability advertises navigation; this endpoint decides access.
  const signedIn = user !== null;
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["private-guide", "mom-bot", user?.member_id],
    queryFn: async () =>
      (await apiClient.get<Document>("/api/guides/mom-bot")).data,
    enabled: signedIn,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });

  if (signedIn && data && !error) {
    const reference = data.sections.find(
      (section) => section.id === "command-reference"
    );
    const guide = {
      ...data,
      sections: data.sections.filter((section) => section !== reference),
    };
    const commands: Document = {
      title: "mom-bot Command Reference",
      audience: data.audience,
      intro: "All mom-bot slash commands, their fields, and who can use them.",
      sections: reference ? [reference] : [],
    };
    const navigationPages = [
      ...guidePages,
      { ...guide, href: "/guide/mom-bot" },
      {
        ...commands,
        href: "/guide/mom-bot/commands",
        parentHref: "/guide/mom-bot",
      },
    ];
    return (
      <GuideDocument
        document={pathname === "/guide/mom-bot/commands" ? commands : guide}
        navigationPages={navigationPages}
      />
    );
  }
  const denied =
    !signedIn || (isAxiosError(error) && error.response?.status === 403);
  return (
    <main className="mx-auto max-w-xl space-y-4 px-6 py-12">
      <h1 className="text-2xl font-bold">
        {denied ? "Guide unavailable" : "Loading guide"}
      </h1>
      <p role="status">
        {denied
          ? "This guide is not available for your account."
          : isPending
            ? "Checking access…"
            : "The guide could not be loaded. Please try again."}
      </p>
      {!denied && error && (
        <button
          onClick={() => void refetch()}
          className="text-violet-700 underline"
        >
          Try again
        </button>
      )}
      <Link to="/guide" className="inline-block text-violet-700 underline">
        Read the General Usage Guide
      </Link>
    </main>
  );
}
