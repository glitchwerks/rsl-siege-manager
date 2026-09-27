import { useState, useEffect, useRef } from "react";
import { useParams, Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getPosts, setPostConditions } from "../api/posts";
import { getPostConditions } from "../api/members";
import { getSiege } from "../api/sieges";
import { getBoard } from "../api/board";
import type { Post, BuildingResponse } from "../api/types";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Checkbox } from "../components/ui/checkbox";
import { Badge } from "../components/ui/badge";
import { ArrowLeft, ChevronDown, ChevronUp } from "lucide-react";
import { GroupByToggle } from "../components/GroupByToggle";
import { groupPostConditions } from "../lib/groupPostConditions";
import type { GroupByMode } from "../lib/groupPostConditions";
import {
  useGroupByPreference,
  GROUP_BY_STORAGE_KEY,
} from "../lib/useGroupByPreference";
import { isSiegeLocked } from "../lib/siege";

const PRIORITY_LABELS: Record<number, string> = {
  0: "Unset",
  1: "Low",
  2: "Medium",
  3: "High",
};

function PostRow({
  post,
  siegeId,
  isLocked,
  initialExpanded = false,
  building,
  forcedMode,
  forcedVersion,
}: {
  post: Post;
  siegeId: number;
  isLocked?: boolean;
  initialExpanded?: boolean;
  building?: BuildingResponse;
  /** The mode last broadcast by the master toggle. */
  forcedMode: GroupByMode;
  /**
   * Incremented each time the master toggle fires an override.
   * Using a version counter (not a direct dep on forcedMode) means the effect
   * only fires when the user explicitly clicks the master — not on every
   * re-render where forcedMode happens to equal the current row value.
   */
  forcedVersion: number;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(initialExpanded);
  const [condFilter, setCondFilter] = useState("");
  const [groupByMode, setGroupByMode] =
    useGroupByPreference(GROUP_BY_STORAGE_KEY);
  // Tracks the last forcedVersion this row applied so the effect does not re-fire
  // when other deps (groupByMode, forcedMode) change after a per-row flip.
  const appliedVersionRef = useRef(0);
  const [selectedConditions, setSelectedConditions] = useState<Set<number>>(
    new Set(post.active_conditions.map((c) => c.id))
  );

  // Master override: when forcedVersion bumps, snap this row's local state to
  // forcedMode. appliedVersionRef tracks the last version we acted on so the
  // effect is a no-op when other deps (groupByMode, forcedMode) change due to a
  // per-row flip — preserving row independence after a master broadcast while
  // still including all deps in the array (no eslint-disable needed).
  useEffect(() => {
    if (forcedVersion > 0 && forcedVersion !== appliedVersionRef.current) {
      appliedVersionRef.current = forcedVersion;
      setGroupByMode(forcedMode);
    }
  }, [forcedVersion, forcedMode, setGroupByMode]);

  const { data: allConditions } = useQuery({
    queryKey: ["postConditions"],
    queryFn: getPostConditions,
    enabled: expanded,
  });

  const condMutation = useMutation({
    mutationFn: () =>
      setPostConditions(siegeId, post.id, Array.from(selectedConditions)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["posts", siegeId] });
    },
  });

  function toggleCondition(id: number) {
    setSelectedConditions((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < 3) {
        next.add(id);
      }
      return next;
    });
  }

  const condGroups = groupPostConditions(allConditions ?? [], groupByMode);

  // Derive the assignment match state from the board data for this building.
  // Flatten all non-reserve, non-disabled positions and find the first assigned one.
  const assignedPosition = building
    ? building.groups
        .flatMap((g) => g.positions)
        .find((p) => !p.is_reserve && !p.is_disabled && p.member_id !== null)
    : undefined;

  // A post may have a reserve slot set even when no regular member is assigned.
  // Reserve positions have is_reserve=true and member_id=null (DB constraint).
  const reservePosition = building
    ? building.groups
        .flatMap((g) => g.positions)
        .find((p) => p.is_reserve && !p.is_disabled)
    : undefined;
  const isReserve = reservePosition != null;

  const matchedCondition =
    assignedPosition && assignedPosition.matched_condition_id !== null
      ? post.active_conditions.find(
          (c) => c.id === assignedPosition.matched_condition_id
        )
      : undefined;

  // matchBadge is the element to render, or null when nothing is assigned.
  // Priority: regular assignment > reserve > nothing.
  const matchBadge =
    assignedPosition == null && isReserve ? (
      <Badge
        variant="default"
        className="shrink-0 border border-teal-200 bg-teal-100 text-xs text-teal-700 hover:bg-teal-100"
      >
        Reserve
      </Badge>
    ) : assignedPosition == null ? null : matchedCondition != null ? (
      <Badge
        variant="default"
        className="shrink-0 border border-green-200 bg-green-100 text-xs text-green-800 hover:bg-green-100"
      >
        ✓ {matchedCondition.description}
      </Badge>
    ) : (
      <Badge
        variant="secondary"
        className="shrink-0 border border-amber-200 bg-amber-50 text-xs text-amber-700"
      >
        No condition match
      </Badge>
    );

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center gap-4 px-4 py-3">
        <span className="text-sm font-semibold text-slate-900">
          Post {post.building_number}
        </span>
        <span className="text-sm text-slate-500">
          Priority: {PRIORITY_LABELS[post.priority] ?? post.priority}
        </span>
        {matchBadge}
        {post.description && (
          <span className="truncate text-sm text-slate-600">
            {post.description}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {post.active_conditions.map((c) => (
            <Badge key={c.id} variant="secondary" className="text-xs">
              {c.description}
            </Badge>
          ))}
          {!isLocked && (
            <button
              className="rounded p-1 hover:bg-slate-100"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? (
                <ChevronUp className="h-4 w-4 text-slate-500" />
              ) : (
                <ChevronDown className="h-4 w-4 text-slate-500" />
              )}
            </button>
          )}
        </div>
      </div>

      {expanded && !isLocked && (
        <div className="space-y-4 border-t border-slate-100 px-4 py-4">
          <div>
            <h4 className="mb-2 text-sm font-medium text-slate-700">
              Conditions (max 3)
            </h4>
            <div className="mb-3 flex items-center gap-2">
              <Input
                placeholder="Filter conditions..."
                value={condFilter}
                onChange={(e) => setCondFilter(e.target.value)}
                className="h-8 flex-1 text-sm"
              />
              <GroupByToggle
                value={groupByMode}
                onChange={setGroupByMode}
                aria-label="Row group-by"
              />
            </div>
            {condGroups.map((group) => {
              const filtered = condFilter
                ? group.items.filter((c) =>
                    c.description
                      .toLowerCase()
                      .includes(condFilter.toLowerCase())
                  )
                : group.items;
              if (filtered.length === 0) return null;
              return (
                <div key={group.heading} className="mb-3">
                  <p className="mb-1 text-xs font-medium text-slate-500">
                    {group.heading}
                  </p>
                  <div className="grid grid-cols-2 gap-1.5">
                    {filtered.map((c) => {
                      const checked = selectedConditions.has(c.id);
                      const disabled = !checked && selectedConditions.size >= 3;
                      return (
                        <div key={c.id} className="flex items-center gap-2">
                          <Checkbox
                            id={`cond-${post.id}-${c.id}`}
                            checked={checked}
                            disabled={disabled}
                            onCheckedChange={() => toggleCondition(c.id)}
                          />
                          <Label
                            htmlFor={`cond-${post.id}-${c.id}`}
                            className="text-xs font-normal"
                          >
                            {c.description}
                          </Label>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => condMutation.mutate()}
              disabled={condMutation.isPending}
            >
              Save Conditions
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function PostsPage() {
  const { id } = useParams<{ id: string }>();
  const siegeId = Number(id);
  const [searchParams] = useSearchParams();
  const expandPostNumber = searchParams.get("post")
    ? Number(searchParams.get("post"))
    : null;

  // Master group-by toggle: a one-shot override for all mounted PostRows.
  // useGroupByPreference is per-component state (useState + localStorage lazy
  // init); per-row toggles are independent after mount. The master broadcasts
  // via broadcastVersion — each PostRow's useEffect fires only when that
  // version bumps, ensuring the override is explicit (not reactive).
  const [masterMode, setMasterMode] =
    useGroupByPreference(GROUP_BY_STORAGE_KEY);
  const [broadcastVersion, setBroadcastVersion] = useState(0);

  function broadcastMode(next: GroupByMode) {
    setMasterMode(next); // writes localStorage via the hook
    setBroadcastVersion((v) => v + 1); // fires the override in every PostRow
  }

  const { data: siege } = useQuery({
    queryKey: ["siege", siegeId],
    queryFn: () => getSiege(siegeId),
  });
  const siegeLocked = isSiegeLocked(siege);

  const {
    data: posts,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["posts", siegeId],
    queryFn: () => getPosts(siegeId),
  });

  const { data: board } = useQuery({
    queryKey: ["board", siegeId],
    queryFn: () => getBoard(siegeId),
  });

  const sorted = posts
    ?.slice()
    .sort((a, b) => a.building_number - b.building_number);

  return (
    <div>
      <div className="mb-6">
        <Link
          to="/sieges"
          className="mb-2 flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Sieges
        </Link>
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-slate-900">
            Posts — Siege #{siegeId}
          </h1>
          <GroupByToggle
            value={masterMode}
            onChange={broadcastMode}
            aria-label="Master group-by"
          />
        </div>
        <p className="mt-1 text-sm text-slate-500">
          Set post conditions for each post in this siege.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          Failed to load posts.
        </div>
      )}

      {isLoading ? (
        <div className="py-12 text-center text-slate-500">Loading...</div>
      ) : sorted?.length === 0 ? (
        <div className="py-12 text-center text-slate-500">
          No posts found for this siege.
        </div>
      ) : (
        <div className="space-y-3">
          {sorted?.map((post) => (
            <PostRow
              key={post.id}
              post={post}
              siegeId={siegeId}
              isLocked={siegeLocked}
              initialExpanded={expandPostNumber === post.building_number}
              building={board?.buildings.find((b) => b.id === post.building_id)}
              forcedMode={masterMode}
              forcedVersion={broadcastVersion}
            />
          ))}
        </div>
      )}
    </div>
  );
}
