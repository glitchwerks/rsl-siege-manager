import { useState, useMemo, type ReactNode } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DndContext,
  DragOverlay,
  useDraggable,
  useDroppable,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { getBoard, updatePosition } from "../api/board";
import {
  getSiege,
  getSiegeMembers,
  previewAutofill,
  applyAutofill,
  validateSiege,
} from "../api/sieges";
import { getPostPriorities } from "../api/posts";
import { PostsTab } from "../components/PostsTab";
import type {
  BuildingType,
  BuildingResponse,
  BuildingGroupResponse,
  PositionResponse,
  SiegeMember,
  AutofillPreviewResult,
  ValidationResult,
} from "../api/types";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "../components/ui/dialog";
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  LayoutGrid,
  MessageSquare,
  Search,
} from "lucide-react";
import { BUILDING_COLORS, BUILDING_LABELS } from "../lib/buildingColors";
import { isSiegeLocked as getIsSiegeLocked } from "../lib/siege";

// ─── Constants ────────────────────────────────────────────────────────────────

const ROLE_LABELS: Record<string, string> = {
  heavy_hitter: "HH",
  advanced: "Adv",
  medium: "Med",
  novice: "Nov",
};

const ROLE_PRIORITY: Record<string, number> = {
  heavy_hitter: 0,
  advanced: 1,
  medium: 2,
  novice: 3,
};

const ROLE_COLORS: Record<string, string> = {
  heavy_hitter: "border-red-500 bg-red-50",
  advanced: "border-amber-500 bg-amber-50",
  medium: "border-green-500 bg-green-50",
  novice: "border-blue-400 bg-blue-50",
};

const ROLE_BADGE_COLORS: Record<string, string> = {
  heavy_hitter: "bg-red-100 text-red-700",
  advanced: "bg-amber-100 text-amber-700",
  medium: "bg-green-100 text-green-700",
  novice: "bg-blue-100 text-blue-700",
};

// Role-colored chip for member name inside a position cell
const ROLE_CHIP_COLORS: Record<string, string> = {
  heavy_hitter: "bg-red-100 text-red-800",
  advanced: "bg-amber-100 text-amber-800",
  medium: "bg-green-100 text-green-800",
  novice: "bg-blue-100 text-blue-800",
};

const POWER_LABELS: Record<string, string> = {
  lt_10m: "<10M",
  "10_15m": "10-15M",
  "16_20m": "16-20M",
  "21_25m": "21-25M",
  gt_25m: ">25M",
};

// Canonical sort order for building sections
const BUILDING_TYPE_ORDER: BuildingType[] = [
  "stronghold",
  "mana_shrine",
  "magic_tower",
  "defense_tower",
];

// ─── PositionCell ──────────────────────────────────────────────────────────────

function PositionCell({
  position,
  siegeId,
  memberRoleMap,
  onUpdate,
  isLocked,
}: {
  position: PositionResponse;
  siegeId: number;
  memberRoleMap: Record<number, string>;
  onUpdate: () => void;
  isLocked?: boolean;
}) {
  const queryClient = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);

  const droppableDisabled = position.is_disabled || !!isLocked;
  const { setNodeRef, isOver } = useDroppable({
    id: `position-${position.id}`,
    disabled: droppableDisabled,
  });

  const mutation = useMutation({
    mutationFn: (data: { member_id?: number | null; is_reserve?: boolean }) =>
      updatePosition(siegeId, position.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["post-suggestions-status"] });
      onUpdate();
      setMenuOpen(false);
    },
  });

  const role =
    position.member_id != null ? memberRoleMap[position.member_id] : undefined;
  const chipColor = role
    ? (ROLE_CHIP_COLORS[role] ?? "bg-slate-100 text-slate-700")
    : "";

  function cellContent() {
    if (position.is_disabled) {
      return (
        <span className="text-xs text-slate-400 line-through">DISABLED</span>
      );
    }
    if (position.is_reserve) {
      return (
        <Badge variant="yellow" className="px-1 py-0 text-xs">
          RESERVE
        </Badge>
      );
    }
    if (position.member_id != null) {
      return (
        <span
          className={`truncate rounded px-1 text-xs font-medium ${chipColor}`}
        >
          {position.member_name}
        </span>
      );
    }
    return <span className="text-xs text-slate-300">—</span>;
  }

  const cellBg = position.is_disabled ? "bg-slate-100" : "bg-white";

  const borderStyle = isOver
    ? "border-violet-400 ring-2 ring-violet-400"
    : position.is_disabled
      ? "border-slate-200"
      : position.member_id != null
        ? "border-slate-200"
        : "border-dashed border-slate-200";

  return (
    <>
      <div
        ref={setNodeRef}
        className={`group relative flex min-h-[28px] items-center justify-between rounded border px-1.5 py-0.5 ${cellBg} ${borderStyle} cursor-default`}
      >
        <span className="mr-1 shrink-0 text-xs text-slate-400">
          {position.position_number}.
        </span>
        <div className="flex min-w-0 flex-1 items-center overflow-hidden">
          {cellContent()}
        </div>
        {!position.is_disabled && !isLocked && (
          <button
            className="ml-0.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
            onClick={() => setMenuOpen(true)}
          >
            <ChevronDown className="h-3 w-3 text-slate-400" />
          </button>
        )}
      </div>

      <Dialog open={menuOpen} onOpenChange={setMenuOpen}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle>Position {position.position_number}</DialogTitle>
            <DialogDescription>
              {position.member_name ?? "Unassigned"}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                mutation.mutate({
                  is_reserve: true,
                  member_id: null,
                })
              }
              disabled={mutation.isPending}
            >
              Mark RESERVE
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                mutation.mutate({
                  member_id: null,
                  is_reserve: false,
                })
              }
              disabled={mutation.isPending}
            >
              Clear
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ─── DraggableMemberRow ────────────────────────────────────────────────────────

function DraggableMemberRow({
  member,
  count,
  scrollLimit,
  isLocked,
}: {
  member: SiegeMember;
  count: number;
  scrollLimit: number;
  isLocked: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({
      id: `member-${member.member_id}`,
      disabled: isLocked,
    });

  const style = transform
    ? { transform: CSS.Translate.toString(transform) }
    : undefined;

  const overLimit = scrollLimit > 0 && count > scrollLimit;
  const roleColor =
    ROLE_COLORS[member.member_role] ?? "border-slate-300 bg-white";
  const badgeColor =
    ROLE_BADGE_COLORS[member.member_role] ?? "bg-slate-100 text-slate-600";
  const tooltip = [
    member.member_power_level
      ? (POWER_LABELS[member.member_power_level] ?? member.member_power_level)
      : null,
    member.attack_day ? `Day ${member.attack_day}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      ref={setNodeRef}
      style={style}
      title={tooltip || undefined}
      className={`flex items-center gap-1.5 border-l-4 px-2 py-1.5 ${overLimit ? "border-red-300 bg-red-50" : roleColor} ${
        isDragging ? "opacity-40" : ""
      } ${!isLocked ? "cursor-grab active:cursor-grabbing" : ""}`}
      {...listeners}
      {...attributes}
    >
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-800">
        {member.member_name}
      </span>
      <span
        className={`shrink-0 rounded px-1 py-0.5 text-xs font-medium ${badgeColor}`}
      >
        {ROLE_LABELS[member.member_role] ?? member.member_role}
      </span>
      <span
        className={`shrink-0 rounded-full px-1.5 py-0 text-xs font-semibold tabular-nums ${
          overLimit ? "bg-red-100 text-red-700" : "bg-slate-100 text-slate-600"
        }`}
      >
        {count}
      </span>
    </div>
  );
}

// ─── MemberDragOverlay ─────────────────────────────────────────────────────────
// Floating chip rendered by DragOverlay while a member is being dragged.

function MemberDragOverlay({ member }: { member: SiegeMember }) {
  const roleColor =
    ROLE_COLORS[member.member_role] ?? "border-slate-300 bg-white";
  const badgeColor =
    ROLE_BADGE_COLORS[member.member_role] ?? "bg-slate-100 text-slate-600";

  return (
    <div
      className={`flex items-center gap-1.5 rounded border-l-4 bg-white px-2 py-1.5 shadow-lg ${roleColor}`}
      style={{ width: "13rem" }}
    >
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-800">
        {member.member_name}
      </span>
      <span
        className={`shrink-0 rounded px-1 py-0.5 text-xs font-medium ${badgeColor}`}
      >
        {ROLE_LABELS[member.member_role] ?? member.member_role}
      </span>
    </div>
  );
}

// ─── MemberBucket ──────────────────────────────────────────────────────────────

type RoleFilter = "all" | "heavy_hitter" | "advanced" | "medium" | "novice";

const ROLE_FILTER_OPTIONS: { value: RoleFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "heavy_hitter", label: "Heavy Hitter" },
  { value: "advanced", label: "Advanced" },
  { value: "medium", label: "Medium" },
  { value: "novice", label: "Novice" },
];

function MemberBucket({
  siegeMembers,
  memberAssignments,
  scrollLimit,
  isLocked,
}: {
  siegeMembers: SiegeMember[];
  memberAssignments: Record<number, number>;
  scrollLimit: number;
  isLocked: boolean;
}) {
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return siegeMembers
      .filter((m) => !q || m.member_name.toLowerCase().includes(q))
      .filter((m) => roleFilter === "all" || m.member_role === roleFilter)
      .slice()
      .sort((a, b) => {
        const roleDiff =
          (ROLE_PRIORITY[a.member_role] ?? 99) -
          (ROLE_PRIORITY[b.member_role] ?? 99);
        if (roleDiff !== 0) return roleDiff;
        const countDiff =
          (memberAssignments[a.member_id] ?? 0) -
          (memberAssignments[b.member_id] ?? 0);
        if (countDiff !== 0) return countDiff;
        return a.member_name.localeCompare(b.member_name);
      });
  }, [siegeMembers, search, roleFilter, memberAssignments]);

  return (
    <div
      className="flex w-52 shrink-0 flex-col"
      style={{
        height: "calc(100vh - 200px)",
        position: "sticky",
        top: "16px",
        alignSelf: "flex-start",
      }}
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Members
        </span>
        <span className="text-xs text-slate-400">{siegeMembers.length}</span>
      </div>

      <div className="relative mb-1.5">
        <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
        <Input
          placeholder="Search..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-7 pl-6 text-xs"
        />
      </div>

      <select
        value={roleFilter}
        onChange={(e) => setRoleFilter(e.target.value as RoleFilter)}
        className="mb-2 h-7 w-full rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-700 focus:outline-none focus:ring-1 focus:ring-violet-400"
      >
        {ROLE_FILTER_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      <div className="flex-1 overflow-y-auto rounded-lg border border-slate-200 bg-white">
        {filtered.length === 0 ? (
          <p className="px-3 py-4 text-center text-xs text-slate-400">
            No members
          </p>
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((m) => (
              <DraggableMemberRow
                key={m.member_id}
                member={m}
                count={memberAssignments[m.member_id] ?? 0}
                scrollLimit={scrollLimit}
                isLocked={isLocked}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── BuildingTableRow ──────────────────────────────────────────────────────────
// Renders one building as a table row (or multi-row if groups > 5).

const MAX_GROUPS_PER_ROW = 5;

function BuildingTableRow({
  building,
  siegeId,
  memberRoleMap,
  onUpdate,
  isLocked,
}: {
  building: BuildingResponse;
  siegeId: number;
  memberRoleMap: Record<number, string>;
  onUpdate: () => void;
  isLocked?: boolean;
}) {
  const colors = BUILDING_COLORS[building.building_type];

  // Chunk groups into rows of MAX_GROUPS_PER_ROW
  const groupChunks: BuildingGroupResponse[][] = [];
  for (let i = 0; i < building.groups.length; i += MAX_GROUPS_PER_ROW) {
    groupChunks.push(building.groups.slice(i, i + MAX_GROUPS_PER_ROW));
  }

  const allPositions = building.groups.flatMap((g) => g.positions);
  const filledCount = allPositions.filter(
    (p) => !p.is_disabled && !p.is_reserve && p.member_id != null
  ).length;
  const activeCount = allPositions.filter((p) => !p.is_disabled).length;

  return (
    <div className={`flex rounded border ${colors.border} overflow-hidden`}>
      {/* Building label — sits in a single column that naturally spans all sub-rows */}
      <div
        className={`${colors.header} flex w-36 shrink-0 flex-col justify-center px-2 py-2 text-white`}
      >
        <p className="text-xs font-semibold leading-tight">
          {BUILDING_LABELS[building.building_type]} {building.building_number}
        </p>
        <p className="mt-0.5 text-xs opacity-75">
          Lv {building.level}
          {building.is_broken ? " · Broken" : ""}
        </p>
        <p className="mt-0.5 text-xs opacity-75">
          {filledCount}/{activeCount} filled
        </p>
      </div>

      {/* Group sub-rows stacked vertically */}
      <div className="flex flex-1 flex-col">
        {groupChunks.map((chunk, chunkIdx) => (
          <div
            key={chunkIdx}
            className={`flex flex-1 ${chunkIdx > 0 ? `border-t ${colors.border}` : ""}`}
          >
            {chunk.map((group) => (
              <div
                key={group.id}
                className={`flex-1 border-l ${colors.border} ${colors.bg} min-w-0 px-1.5 py-1.5`}
              >
                <p className="mb-1 text-center text-xs font-medium text-slate-500">
                  G{group.group_number}
                </p>
                <div className="space-y-0.5">
                  {group.positions.map((pos) => (
                    <PositionCell
                      key={pos.id}
                      position={pos}
                      siegeId={siegeId}
                      memberRoleMap={memberRoleMap}
                      onUpdate={onUpdate}
                      isLocked={isLocked}
                    />
                  ))}
                </div>
              </div>
            ))}
            {/* Pad empty columns so rows are uniform width when fewer than max groups */}
            {chunk.length < MAX_GROUPS_PER_ROW &&
              Array.from({ length: MAX_GROUPS_PER_ROW - chunk.length }).map(
                (_, i) => (
                  <div
                    key={`pad-${i}`}
                    className="flex-1 border-l border-transparent"
                  />
                )
              )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── BuildingTypeSection ───────────────────────────────────────────────────────

function BuildingTypeSection({
  type,
  buildings,
  siegeId,
  memberRoleMap,
  onUpdate,
  isLocked,
  defaultExpanded,
}: {
  type: BuildingType;
  buildings: BuildingResponse[];
  siegeId: number;
  memberRoleMap: Record<number, string>;
  onUpdate: () => void;
  isLocked?: boolean;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const colors = BUILDING_COLORS[type];

  return (
    <div className="mb-4">
      {/* Section header */}
      <div
        className={`flex cursor-pointer select-none items-center gap-2 rounded-t border px-3 py-2 ${colors.sectionHeader}`}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="text-slate-500">
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </span>
        <span className="text-sm font-semibold uppercase tracking-wide">
          {BUILDING_LABELS[type]}
        </span>
        <span className="text-xs opacity-70">({buildings.length})</span>
      </div>

      {expanded && (
        <div className="space-y-1 rounded-b border-x border-b border-slate-200 bg-white p-2">
          {buildings.map((b) => (
            <BuildingTableRow
              key={b.id}
              building={b}
              siegeId={siegeId}
              memberRoleMap={memberRoleMap}
              onUpdate={onUpdate}
              isLocked={isLocked}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── BuildingsTab ──────────────────────────────────────────────────────────────

function BuildingsTab({
  buildings,
  siegeId,
  memberRoleMap,
  onUpdate,
  isLocked,
}: {
  buildings: BuildingResponse[];
  siegeId: number;
  memberRoleMap: Record<number, string>;
  onUpdate: () => void;
  isLocked?: boolean;
}) {
  const nonPostBuildings = buildings.filter((b) => b.building_type !== "post");

  if (nonPostBuildings.length === 0) {
    return (
      <div className="py-12 text-center text-slate-500">
        No buildings configured.{" "}
        <Link
          to={`/sieges/${siegeId}`}
          className="text-violet-600 hover:underline"
        >
          Add buildings
        </Link>{" "}
        in siege settings.
      </div>
    );
  }

  // Group and sort buildings
  const grouped: Partial<Record<BuildingType, BuildingResponse[]>> = {};
  for (const b of nonPostBuildings) {
    if (!grouped[b.building_type]) grouped[b.building_type] = [];
    grouped[b.building_type]!.push(b);
  }

  // Sort each group by building_number
  for (const arr of Object.values(grouped)) {
    arr?.sort((a, b) => a.building_number - b.building_number);
  }

  return (
    <div>
      {BUILDING_TYPE_ORDER.filter(
        (t) => grouped[t] && grouped[t]!.length > 0
      ).map((type) => (
        <BuildingTypeSection
          key={type}
          type={type}
          buildings={grouped[type]!}
          siegeId={siegeId}
          memberRoleMap={memberRoleMap}
          onUpdate={onUpdate}
          isLocked={isLocked}
          defaultExpanded={true}
        />
      ))}
    </div>
  );
}

// ─── ConditionalDndContext ─────────────────────────────────────────────────────
// Renders children inside a DndContext when active=true, or unwrapped otherwise.
// This ensures DnD sensors and event listeners are only active on the buildings tab.

function ConditionalDndContext({
  sensors,
  onDragStart,
  onDragEnd,
  onDragCancel,
  overlay,
  children,
}: {
  sensors: ReturnType<typeof useSensors>;
  onDragStart: (event: DragStartEvent) => void;
  onDragEnd: (event: DragEndEvent) => void;
  onDragCancel: () => void;
  overlay: ReactNode;
  children: ReactNode;
}) {
  // Always render DndContext — MemberBucket uses useDraggable hooks
  // which require a DndContext ancestor at all times.
  return (
    <DndContext
      sensors={sensors}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={onDragCancel}
    >
      {children}
      <DragOverlay>{overlay}</DragOverlay>
    </DndContext>
  );
}

// ─── BoardPage ─────────────────────────────────────────────────────────────────

type ActiveTab = "buildings" | "posts";

export default function BoardPage() {
  const { id } = useParams<{ id: string }>();
  const siegeId = Number(id);
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<ActiveTab>("buildings");
  const [autofillPreview, setAutofillPreview] =
    useState<AutofillPreviewResult | null>(null);
  const [autofillOpen, setAutofillOpen] = useState(false);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validationOpen, setValidationOpen] = useState(false);
  const [activeMemberId, setActiveMemberId] = useState<number | null>(null);

  const { data: board, isLoading: boardLoading } = useQuery({
    queryKey: ["board", siegeId],
    queryFn: () => getBoard(siegeId),
  });

  const { data: siegeMembers } = useQuery({
    queryKey: ["siegeMembers", siegeId],
    queryFn: () => getSiegeMembers(siegeId),
  });

  const { data: siege } = useQuery({
    queryKey: ["siege", siegeId],
    queryFn: () => getSiege(siegeId),
  });

  const { data: postPriorities } = useQuery({
    queryKey: ["postPriorities"],
    queryFn: getPostPriorities,
  });

  // Unused in this refactor but kept to avoid breaking existing query patterns
  void postPriorities;

  const previewMutation = useMutation({
    mutationFn: () => previewAutofill(siegeId),
    onSuccess: (data) => {
      setAutofillPreview(data);
      setAutofillOpen(true);
    },
  });

  const applyMutation = useMutation({
    mutationFn: () => applyAutofill(siegeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["post-suggestions-status"] });
      setAutofillOpen(false);
      setAutofillPreview(null);
    },
  });

  const validateMutation = useMutation({
    mutationFn: () => validateSiege(siegeId),
    onSuccess: (data) => {
      setValidation(data);
      setValidationOpen(true);
    },
  });

  // ── Derived stats ────────────────────────────────────────────────────────────

  const allPositions = useMemo(
    () =>
      board?.buildings.flatMap((b) => b.groups.flatMap((g) => g.positions)) ??
      [],
    [board]
  );

  const totalSlots = allPositions.length;
  const assignedCount = allPositions.filter(
    (p) => !p.is_disabled && !p.is_reserve && p.member_id != null
  ).length;
  const reserveCount = allPositions.filter((p) => p.is_reserve).length;
  const emptyCount = allPositions.filter(
    (p) => !p.is_disabled && !p.is_reserve && p.member_id == null
  ).length;
  // Per-member assignment counts (all buildings including posts)
  const memberAssignments = useMemo(() => {
    const counts: Record<number, number> = {};
    for (const pos of allPositions) {
      if (pos.member_id != null && !pos.is_reserve && !pos.is_disabled) {
        counts[pos.member_id] = (counts[pos.member_id] ?? 0) + 1;
      }
    }
    return counts;
  }, [allPositions]);

  // Map member_id → role (for coloring position chips)
  const memberRoleMap = useMemo(() => {
    const map: Record<number, string> = {};
    for (const m of siegeMembers ?? []) {
      map[m.member_id] = m.member_role;
    }
    return map;
  }, [siegeMembers]);

  // Build lookups for autofill preview dialog
  const positionContextLookup = useMemo(() => {
    const map: Record<
      number,
      {
        buildingType: string;
        buildingNumber: number;
        groupNumber: number;
        positionNumber: number;
      }
    > = {};
    if (!board) return map;
    for (const building of board.buildings) {
      for (const group of building.groups) {
        for (const pos of group.positions) {
          map[pos.id] = {
            buildingType: building.building_type,
            buildingNumber: building.building_number,
            groupNumber: group.group_number,
            positionNumber: pos.position_number,
          };
        }
      }
    }
    return map;
  }, [board]);

  const memberLookup = useMemo(() => {
    const map: Record<number, string> = {};
    for (const m of siegeMembers ?? []) map[m.member_id] = m.member_name;
    return map;
  }, [siegeMembers]);

  const sortedSiegeMembers = useMemo(
    () =>
      (siegeMembers ?? [])
        .slice()
        .sort((a, b) => a.member_name.localeCompare(b.member_name)),
    [siegeMembers]
  );

  const totalScrolls = siege?.computed_scroll_count ?? 0;
  const scrollsPerMember = totalScrolls < 90 ? 3 : 4;
  const isLocked = getIsSiegeLocked(siege);

  // The member currently being dragged (for the DragOverlay chip)
  const activeMember = useMemo(
    () =>
      activeMemberId != null
        ? (sortedSiegeMembers.find((m) => m.member_id === activeMemberId) ??
          null)
        : null,
    [activeMemberId, sortedSiegeMembers]
  );

  // ── DnD ──────────────────────────────────────────────────────────────────────

  const sensors = useSensors(
    useSensor(PointerSensor, {
      // Short distance before activation so chevron button clicks still work
      activationConstraint: { distance: 6 },
    })
  );

  function handleDragStart(event: DragStartEvent) {
    document.body.style.overflowX = "hidden";
    const activeId = String(event.active.id);
    if (activeId.startsWith("member-")) {
      setActiveMemberId(Number(activeId.replace("member-", "")));
    }
  }

  function handleDragEnd(event: DragEndEvent) {
    document.body.style.overflowX = "";
    setActiveMemberId(null);
    const { active, over } = event;
    if (!over) return;

    const activeId = String(active.id);
    const overId = String(over.id);

    if (!activeId.startsWith("member-") || !overId.startsWith("position-"))
      return;

    const memberId = Number(activeId.replace("member-", ""));
    const positionId = Number(overId.replace("position-", ""));
    if (!memberId || !positionId) return;

    updatePosition(siegeId, positionId, {
      member_id: memberId,
      is_reserve: false,
    }).then(() => {
      queryClient.invalidateQueries({ queryKey: ["board", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["post-suggestions-status"] });
    });
  }

  function refreshBoard() {
    // board is already invalidated inside PositionCell's mutation
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  if (boardLoading) {
    return (
      <div className="py-12 text-center text-slate-500">Loading board...</div>
    );
  }

  return (
    <div>
      {/* ── Header ── */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <Link
            to="/sieges"
            className="mb-2 flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Sieges
          </Link>
          <h1 className="text-xl font-bold text-slate-900">
            Board — Siege {siege?.date ?? `#${siegeId}`}
          </h1>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => validateMutation.mutate()}
            disabled={validateMutation.isPending}
          >
            {validateMutation.isPending ? "Validating..." : "Validate"}
          </Button>
          <Button
            size="sm"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending || isLocked}
          >
            {previewMutation.isPending ? "Loading..." : "Preview Auto-fill"}
          </Button>
        </div>
      </div>

      {/* ── Summary bar ── */}
      <div className="mb-4 flex flex-wrap gap-3 rounded-lg border border-slate-200 bg-white px-4 py-2.5">
        <span className="text-sm text-slate-600">
          <span className="font-semibold text-slate-900">{totalSlots}</span>{" "}
          total
        </span>
        <span className="text-sm text-slate-600">
          <span className="font-semibold text-green-700">{assignedCount}</span>{" "}
          assigned
        </span>
        <span className="text-sm text-slate-600">
          <span className="font-semibold text-amber-600">{reserveCount}</span>{" "}
          reserve
        </span>
        <span className="text-sm text-slate-600">
          <span className="font-semibold text-orange-600">{emptyCount}</span>{" "}
          empty
        </span>
        {totalScrolls > 0 && (
          <span className="text-sm text-slate-600">
            <span className="font-semibold text-violet-700">
              {totalScrolls}
            </span>{" "}
            scrolls
            {scrollsPerMember > 0 && (
              <>
                {" "}
                ·{" "}
                <span className="font-semibold text-violet-700">
                  {scrollsPerMember}
                </span>
                /member
              </>
            )}
          </span>
        )}
      </div>

      {/* ── Main two-column layout ── */}
      {/* DndContext is only active on the buildings tab */}
      <ConditionalDndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={() => {
          document.body.style.overflowX = "";
          setActiveMemberId(null);
        }}
        overlay={
          activeMember ? <MemberDragOverlay member={activeMember} /> : null
        }
      >
        <div className="flex items-start gap-4">
          {/* Left: Member Bucket */}
          <MemberBucket
            siegeMembers={sortedSiegeMembers}
            memberAssignments={memberAssignments}
            scrollLimit={scrollsPerMember}
            isLocked={isLocked}
          />

          {/* Right: Tab shell + content */}
          <div className="min-w-0 flex-1">
            {/* Tab bar */}
            <div className="mb-3 flex border-b border-slate-200">
              <button
                className={`flex items-center gap-1.5 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === "buildings"
                    ? "border-violet-600 text-violet-700"
                    : "border-transparent text-slate-500 hover:text-slate-700"
                }`}
                onClick={() => setActiveTab("buildings")}
              >
                <LayoutGrid className="h-4 w-4" />
                Buildings
              </button>
              <button
                className={`flex items-center gap-1.5 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === "posts"
                    ? "border-violet-600 text-violet-700"
                    : "border-transparent text-slate-500 hover:text-slate-700"
                }`}
                onClick={() => setActiveTab("posts")}
              >
                <MessageSquare className="h-4 w-4" />
                Posts
              </button>
            </div>

            {/* Tab content */}
            {activeTab === "buildings" && (
              <BuildingsTab
                buildings={board?.buildings ?? []}
                siegeId={siegeId}
                memberRoleMap={memberRoleMap}
                onUpdate={refreshBoard}
                isLocked={isLocked}
              />
            )}
            {activeTab === "posts" && (
              <PostsTab
                buildings={board?.buildings ?? []}
                siegeId={siegeId}
                siegeMembers={sortedSiegeMembers}
                isLocked={isLocked}
                memberRoleMap={memberRoleMap}
              />
            )}
          </div>
        </div>
      </ConditionalDndContext>

      {/* ── Autofill preview dialog ── */}
      <Dialog open={autofillOpen} onOpenChange={setAutofillOpen}>
        <DialogContent className="max-h-[80vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Auto-fill Preview</DialogTitle>
            <DialogDescription>
              {autofillPreview?.assignments.length ?? 0} assignments proposed.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1">
            {autofillPreview?.assignments.map((a) => {
              const member =
                a.member_id != null ? memberLookup[a.member_id] : null;
              return (
                <div
                  key={a.position_id}
                  className="flex items-center justify-between rounded-sm px-2 py-1 hover:bg-slate-50"
                >
                  <span className="text-sm text-slate-600">
                    {(() => {
                      const ctx = positionContextLookup[a.position_id];
                      if (!ctx) return `Position ${a.position_id}`;
                      const label =
                        BUILDING_LABELS[
                          ctx.buildingType as keyof typeof BUILDING_LABELS
                        ] ?? ctx.buildingType;
                      return `${label} ${ctx.buildingNumber} · G${ctx.groupNumber} · Pos ${ctx.positionNumber}`;
                    })()}
                  </span>
                  <span className="text-sm font-medium">
                    {a.is_reserve ? (
                      <Badge variant="yellow">RESERVE</Badge>
                    ) : member ? (
                      member
                    ) : (
                      <span className="text-slate-400">Unassigned</span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAutofillOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => applyMutation.mutate()}
              disabled={applyMutation.isPending}
            >
              {applyMutation.isPending ? "Applying..." : "Apply"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Validation dialog ── */}
      <Dialog open={validationOpen} onOpenChange={setValidationOpen}>
        <DialogContent className="max-h-[80vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Validation Results</DialogTitle>
          </DialogHeader>
          {validation && (
            <div className="space-y-2">
              {validation.errors.length === 0 &&
                validation.warnings.length === 0 && (
                  <p className="text-sm text-green-600">No issues found.</p>
                )}
              {validation.errors.map((e, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 rounded-md bg-red-50 px-3 py-2"
                >
                  <Badge variant="destructive" className="mt-0.5 shrink-0">
                    Error {e.rule}
                  </Badge>
                  <p className="text-sm text-red-700">{e.message}</p>
                </div>
              ))}
              {validation.warnings.map((w, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 rounded-md bg-yellow-50 px-3 py-2"
                >
                  <Badge variant="yellow" className="mt-0.5 shrink-0">
                    Warning {w.rule}
                  </Badge>
                  <p className="text-sm text-yellow-800">{w.message}</p>
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setValidationOpen(false)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
