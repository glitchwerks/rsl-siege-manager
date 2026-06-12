import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  getSiege,
  getSiegeMembers,
  addSiegeMember,
  updateSiegeMember,
  previewAttackDay,
  applyAttackDay,
} from "../api/sieges";
import { getMembers } from "../api/members";
import type { SiegeMember, AttackDayPreviewResult } from "../api/types";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "../components/ui/dialog";
import { ArrowLeft, Lock, UserPlus, Loader2, UserX } from "lucide-react";

function AttackDaySelect({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  return (
    <Select
      value={value != null ? String(value) : "none"}
      onValueChange={(v) => onChange(v === "none" ? null : Number(v))}
    >
      <SelectTrigger className="h-7 w-20 text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="none">-</SelectItem>
        <SelectItem value="1">Day 1</SelectItem>
        <SelectItem value="2">Day 2</SelectItem>
      </SelectContent>
    </Select>
  );
}

function SiegeMemberRow({
  member,
  siegeId,
  isLocked,
}: {
  member: SiegeMember;
  siegeId: number;
  isLocked?: boolean;
}) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (data: {
      attack_day?: number | null;
      has_reserve_set?: boolean | null;
      attack_day_override?: boolean;
    }) => updateSiegeMember(siegeId, member.member_id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["siegeMembers", siegeId] });
    },
  });

  return (
    <TableRow>
      <TableCell className="font-medium">
        <span className={member.member_is_active === false ? "text-slate-500" : undefined}>
          {member.member_name}
        </span>
        {member.member_is_active === false && (
          <UserX
            className="ml-1 inline-block h-3.5 w-3.5 text-slate-500"
            aria-label="Inactive member"
          />
        )}
      </TableCell>
      <TableCell className="text-sm text-slate-600">
        {{
          heavy_hitter: "Heavy Hitter",
          advanced: "Advanced",
          medium: "Medium",
          novice: "Novice",
        }[member.member_role] ?? member.member_role}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1.5">
          {isLocked ? (
            <span className="text-sm text-slate-600">
              {member.attack_day ? `Day ${member.attack_day}` : "-"}
            </span>
          ) : (
            <AttackDaySelect
              value={member.attack_day}
              onChange={(v) => mutation.mutate({ attack_day: v })}
            />
          )}
          {member.attack_day_override && (
            <Lock className="h-3.5 w-3.5 text-slate-500" aria-label="Pinned" />
          )}
        </div>
      </TableCell>
      <TableCell>
        <Checkbox
          checked={Boolean(member.attack_day_override)}
          disabled={isLocked}
          onCheckedChange={
            isLocked
              ? undefined
              : (v) => mutation.mutate({ attack_day_override: Boolean(v) })
          }
        />
      </TableCell>
      <TableCell>
        <Checkbox
          checked={Boolean(member.has_reserve_set)}
          disabled={isLocked}
          onCheckedChange={
            isLocked
              ? undefined
              : (v) => mutation.mutate({ has_reserve_set: Boolean(v) })
          }
        />
      </TableCell>
    </TableRow>
  );
}

export default function SiegeMembersPage() {
  const { id } = useParams<{ id: string }>();
  const siegeId = Number(id);
  const queryClient = useQueryClient();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState<AttackDayPreviewResult | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [selectedMemberId, setSelectedMemberId] = useState<string>("");
  const [addError, setAddError] = useState<string | null>(null);
  const [reserveAssigning, setReserveAssigning] = useState(false);

  const { data: siege } = useQuery({
    queryKey: ["siege", siegeId],
    queryFn: () => getSiege(siegeId),
  });

  const {
    data: members,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["siegeMembers", siegeId],
    queryFn: () => getSiegeMembers(siegeId),
  });

  // Only fetch available members when the add dialog is open and siege is planning
  const { data: allMembers } = useQuery({
    queryKey: ["members", { is_active: true }],
    queryFn: () => getMembers({ is_active: true }),
    enabled: addOpen && siege?.status === "planning",
  });

  const siegeMemberIds = new Set(members?.map((m) => m.member_id) ?? []);
  const availableMembers = (allMembers ?? [])
    .filter((m) => !siegeMemberIds.has(m.id))
    .sort((a, b) => a.name.localeCompare(b.name));

  const addMutation = useMutation({
    mutationFn: (memberId: number) => addSiegeMember(siegeId, memberId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["siegeMembers", siegeId] });
      setAddOpen(false);
      setSelectedMemberId("");
      setAddError(null);
    },
    onError: (err: unknown) => {
      const message =
        err instanceof Error
          ? err.message
          : "Failed to add member. Please try again.";
      setAddError(message);
    },
  });

  const previewMutation = useMutation({
    mutationFn: () => previewAttackDay(siegeId),
    onSuccess: (data) => {
      setPreview(data);
      setPreviewOpen(true);
    },
  });

  const applyMutation = useMutation({
    mutationFn: () => applyAttackDay(siegeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["siegeMembers", siegeId] });
      setPreviewOpen(false);
      setPreview(null);
    },
  });

  async function handleAutoAssignReserves() {
    const day2Members = (members ?? []).filter((m) => m.attack_day === 2);
    if (day2Members.length === 0) return;
    setReserveAssigning(true);
    try {
      for (const m of day2Members) {
        await updateSiegeMember(siegeId, m.member_id, {
          has_reserve_set: true,
        });
      }
      queryClient.invalidateQueries({ queryKey: ["siegeMembers", siegeId] });
    } finally {
      setReserveAssigning(false);
    }
  }

  function handleAddConfirm() {
    if (!selectedMemberId) return;
    setAddError(null);
    addMutation.mutate(Number(selectedMemberId));
  }

  function handleAddOpenChange(open: boolean) {
    setAddOpen(open);
    if (!open) {
      setSelectedMemberId("");
      setAddError(null);
    }
  }

  const isPlanning = siege?.status === "planning";

  // Build a quick name lookup for the preview dialog
  const nameLookup: Record<number, string> = {};
  for (const m of members ?? []) {
    nameLookup[m.member_id] = m.member_name;
  }

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <Link
            to="/sieges"
            className="mb-2 flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Sieges
          </Link>
          <h1 className="text-2xl font-bold text-slate-900">
            Members — Siege {siege?.date ?? `#${siegeId}`}
          </h1>
        </div>
        <div className="flex gap-2">
          {isPlanning && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAddOpen(true)}
            >
              <UserPlus className="mr-1.5 h-4 w-4" />
              Add Member
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={handleAutoAssignReserves}
            disabled={reserveAssigning || siege?.status === "complete"}
          >
            {reserveAssigning ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                Assigning...
              </>
            ) : (
              "Auto-Assign Reserves"
            )}
          </Button>
          <Button
            size="sm"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending || siege?.status === "complete"}
          >
            {previewMutation.isPending
              ? "Loading..."
              : "Auto-Assign Attack Days"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          Failed to load members.
        </div>
      )}

      {isLoading ? (
        <div className="py-12 text-center text-slate-500">Loading...</div>
      ) : (
        <div className="rounded-lg border border-slate-200 bg-white">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Attack Day</TableHead>
                <TableHead>Override (Pinned)</TableHead>
                <TableHead>Reserve Set</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members?.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="py-8 text-center text-slate-500"
                  >
                    No members in this siege.
                  </TableCell>
                </TableRow>
              )}
              {members
                ?.slice()
                .sort((a, b) => a.member_name.localeCompare(b.member_name))
                .map((m) => (
                  <SiegeMemberRow
                    key={m.member_id}
                    member={m}
                    siegeId={siegeId}
                    isLocked={siege?.status === "complete"}
                  />
                ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Add Member Dialog */}
      <Dialog open={addOpen} onOpenChange={handleAddOpenChange}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Add Member to Siege</DialogTitle>
            <DialogDescription>
              Select an active member who is not yet in this siege.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Select
              value={selectedMemberId}
              onValueChange={setSelectedMemberId}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a member..." />
              </SelectTrigger>
              <SelectContent>
                {availableMembers.length === 0 ? (
                  <div className="px-3 py-2 text-sm text-slate-500">
                    No available members to add.
                  </div>
                ) : (
                  availableMembers.map((m) => (
                    <SelectItem key={m.id} value={String(m.id)}>
                      {m.name}
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
            {addError && (
              <p className="mt-2 text-sm text-red-600">{addError}</p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => handleAddOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={handleAddConfirm}
              disabled={!selectedMemberId || addMutation.isPending}
            >
              {addMutation.isPending ? "Adding..." : "Add"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Attack Day Preview Dialog */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-h-[80vh] max-w-md overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Attack Day Preview</DialogTitle>
            <DialogDescription>
              {preview?.assignments.length ?? 0} members will be assigned.
            </DialogDescription>
          </DialogHeader>
          {preview &&
            (() => {
              const byName = (
                a: { member_id: number },
                b: { member_id: number }
              ) =>
                (nameLookup[a.member_id] ?? "").localeCompare(
                  nameLookup[b.member_id] ?? ""
                );
              const day1 = preview.assignments
                .filter((a) => a.attack_day === 1)
                .sort(byName);
              const day2 = preview.assignments
                .filter((a) => a.attack_day === 2)
                .sort(byName);
              return (
                <div className="grid grid-cols-2 divide-x divide-slate-200 rounded-md border border-slate-200">
                  <div>
                    <div className="border-b border-slate-200 px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Day 1 ({day1.length})
                    </div>
                    {day1.map((a, i) => (
                      <div
                        key={i}
                        className="px-3 py-1.5 text-sm odd:bg-slate-50"
                      >
                        {nameLookup[a.member_id] ?? `Member ${a.member_id}`}
                      </div>
                    ))}
                  </div>
                  <div>
                    <div className="border-b border-slate-200 px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Day 2 ({day2.length})
                    </div>
                    {day2.map((a, i) => (
                      <div
                        key={i}
                        className="px-3 py-1.5 text-sm odd:bg-slate-50"
                      >
                        {nameLookup[a.member_id] ?? `Member ${a.member_id}`}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewOpen(false)}>
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
    </div>
  );
}
