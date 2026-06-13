import { useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { changelog } from "virtual:changelog";
import type { ChangelogEntry } from "virtual:changelog";
import { cn } from "../lib/utils";
import { fetchChangelogStatus, markChangelogSeen } from "../api/changelog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { BulletMarkdown } from "./changelog-markdown";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Return true when the user has not yet seen the latest changelog entry.
 *
 * The comparison is a lexicographic ISO-string compare: both "YYYY-MM-DD" and
 * full ISO8601 timestamps sort consistently because the most-significant part
 * (year) comes first.
 *
 * @param latestReleaseDate - releaseDate of the latest ChangelogEntry (e.g. "2026-05-01").
 * @param lastSeenAt - ISO timestamp the user last dismissed the changelog, or null.
 * @returns true if the user has unread changes.
 */
function hasUnread(
  latestReleaseDate: string,
  lastSeenAt: string | null
): boolean {
  if (!lastSeenAt) return true;
  // Truncate the lastSeenAt to date-only for comparison so "2026-05-01T00:00:00"
  // counts as equal to releaseDate "2026-05-01" (i.e. not unread).
  const seenDate = lastSeenAt.slice(0, 10);
  return seenDate < latestReleaseDate;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Bell-icon button that opens a dropdown listing changelog entries.
 *
 * Behaviour:
 * - Shows a red unread indicator when the latest release is newer than the
 *   user's last-seen timestamp (or they have never seen any entry).
 * - Opening the dropdown fires POST /api/changelog/mark-seen once per
 *   component lifetime (session).  Closing does not re-fire.
 * - Optimistic UI: the indicator clears immediately on open; rolled back on
 *   network error.
 */
export default function ChangelogDropdown() {
  const queryClient = useQueryClient();

  // Once-per-session guard: set to true the first time the dropdown opens.
  const hasFiredMarkSeen = useRef(false);

  // Optimistic override: when non-null, this value supersedes the server's
  // last_seen_changelog_at for the indicator calculation.
  const [optimisticallySeenAt, setOptimisticallySeenAt] = useState<
    string | null
  >(null);

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const { data: status } = useQuery({
    queryKey: ["changelog-status"],
    queryFn: fetchChangelogStatus,
    staleTime: Infinity,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: markChangelogSeen,
    onSuccess: (data) => {
      // Update the canonical cache with the server-confirmed timestamp.
      queryClient.setQueryData(["changelog-status"], data);
    },
    onError: () => {
      // Rollback: clear the optimistic value so the indicator reappears.
      setOptimisticallySeenAt(null);
    },
  });

  // ---------------------------------------------------------------------------
  // Derived state
  // ---------------------------------------------------------------------------

  const latestEntry: ChangelogEntry | undefined = changelog[0];

  // Effective last-seen: prefer the optimistic value when set.
  const effectiveLastSeen =
    optimisticallySeenAt ?? status?.last_seen_changelog_at ?? null;

  const showUnread =
    latestEntry !== undefined &&
    latestEntry.releaseDate !== "" &&
    hasUnread(latestEntry.releaseDate, effectiveLastSeen);

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  function handleOpenChange(open: boolean) {
    if (!open) return; // Closing does not trigger mark-seen.

    // Optimistic clear.
    setOptimisticallySeenAt(new Date().toISOString());

    // Fire once per session.
    if (!hasFiredMarkSeen.current) {
      hasFiredMarkSeen.current = true;
      mutation.mutate();
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <DropdownMenu onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild>
        <button
          data-testid="changelog-button"
          className={cn(
            "relative flex items-center rounded-md p-1.5 text-slate-400 transition-colors",
            "hover:bg-slate-50 hover:text-slate-600",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          )}
          aria-label="What's new"
          title="What's new"
        >
          <Bell className="h-3.5 w-3.5" />
          {showUnread && (
            <span
              data-testid="changelog-unread-dot"
              className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-red-500"
              aria-hidden="true"
            />
          )}
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        className="max-h-[70vh] w-80 overflow-y-auto p-0"
      >
        <div className="border-b border-slate-100 px-3 py-2">
          <p className="text-sm font-semibold text-slate-900">
            What&apos;s new
          </p>
        </div>

        <div className="divide-y divide-slate-100">
          {changelog.map((entry) => (
            <div key={entry.version} className="px-3 py-3">
              <div className="mb-2 flex items-baseline gap-2">
                <span className="text-sm font-semibold text-slate-900">
                  v{entry.version}
                </span>
                {entry.releaseDate && (
                  <span className="text-xs text-slate-400">
                    {entry.releaseDate}
                  </span>
                )}
              </div>

              {Object.entries(entry.sections).map(([section, bullets]) => (
                <div key={section} className="mb-2 last:mb-0">
                  <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
                    {section}
                  </p>
                  <ul className="space-y-0.5">
                    {bullets.map((bullet, idx) => (
                      <li
                        key={idx}
                        className="flex items-start gap-1.5 text-xs text-slate-700"
                      >
                        <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
                        <BulletMarkdown>{bullet}</BulletMarkdown>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
