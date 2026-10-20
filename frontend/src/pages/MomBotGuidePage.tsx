import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { isAxiosError } from "axios";
import { useAuth } from "../context/AuthContext";
import apiClient from "../api/client";
import GuideDocument from "../components/GuideDocument";
import type { GuideDocument as Document } from "../guides/types";

export default function MomBotGuidePage() {
  const { user } = useAuth();
  const allowed = user?.guides?.mom_bot === true;
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["private-guide", "mom-bot", user?.member_id],
    queryFn: async () =>
      (await apiClient.get<Document>("/api/guides/mom-bot")).data,
    enabled: allowed,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });

  if (allowed && data && !error) return <GuideDocument document={data} />;
  const denied =
    !allowed || (isAxiosError(error) && error.response?.status === 403);
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
