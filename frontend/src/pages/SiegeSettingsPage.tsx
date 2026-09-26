import { useState, useEffect } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getSiege,
  updateSiege,
  deleteSiege,
  activateSiege,
  completeSiege,
  cloneSiege,
  reopenSiege,
  validateSiege,
  getBuildings,
  updateBuilding,
  getSiegeMembers,
} from "../api/sieges";
import {
  notifySiegeMembers,
  getNotificationBatch,
  postToChannel,
  generateImages,
} from "../api/notifications";
import type {
  ValidationIssue,
  ValidationResult,
  NotifyResponse,
  NotificationResultItem,
} from "../api/types";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Checkbox } from "../components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "../components/ui/dialog";
import { Badge } from "../components/ui/badge";
import {
  ArrowLeft,
  MessageSquare,
  Check,
  X,
  Loader2,
  AlertCircle,
  Download,
  Send,
  Image,
} from "lucide-react";
import { isAxiosError } from "axios";
import { cn } from "../lib/utils";
import { BUILDING_COLORS, BUILDING_LABELS } from "../lib/buildingColors";
import { isSiegeLocked } from "../lib/siege";

export default function SiegeSettingsPage() {
  const { id } = useParams<{ id: string }>();
  const siegeId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [date, setDate] = useState("");
  const [settingsError, setSettingsError] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cloneConfirmOpen, setCloneConfirmOpen] = useState(false);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [activateErrors, setActivateErrors] = useState<ValidationIssue[]>([]);

  // Notification state
  const [notifyConfirmOpen, setNotifyConfirmOpen] = useState(false);
  const [postConfirmOpen, setPostConfirmOpen] = useState(false);
  const [notifyBatch, setNotifyBatch] = useState<NotifyResponse | null>(null);
  const [postChannelResult, setPostChannelResult] = useState<string | null>(
    null
  );
  const [postChannelError, setPostChannelError] = useState<string | null>(null);
  const [notifyError, setNotifyError] = useState<string | null>(null);
  const [generatedImages, setGeneratedImages] = useState<{
    assignments_image: string;
    reserves_image: string;
  } | null>(null);

  const { data: siege, isLoading: siegeLoading } = useQuery({
    queryKey: ["siege", siegeId],
    queryFn: () => getSiege(siegeId),
  });
  const siegeLocked = isSiegeLocked(siege);

  const { data: buildings } = useQuery({
    queryKey: ["buildings", siegeId],
    queryFn: () => getBuildings(siegeId),
  });

  const { data: siegeMembers } = useQuery({
    queryKey: ["siegeMembers", siegeId],
    queryFn: () => getSiegeMembers(siegeId),
  });

  // Notification batch polling — runs until all results have success !== null or status === "completed"
  const batchDone = (results: NotificationResultItem[], status: string) =>
    status === "completed" ||
    (results.length > 0 && results.every((r) => r.success !== null));

  const { data: batchData } = useQuery({
    queryKey: ["notificationBatch", siegeId, notifyBatch?.batch_id],
    queryFn: () => getNotificationBatch(siegeId, notifyBatch!.batch_id),
    enabled: notifyBatch != null,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 3000;
      return batchDone(data.results, data.status) ? false : 3000;
    },
  });

  const batchInProgress =
    notifyBatch !== null &&
    !batchDone(
      batchData?.results ?? [],
      batchData?.status ?? notifyBatch?.status ?? ""
    );

  const batchComplete = batchData?.status === "completed";

  useEffect(() => {
    if (siege) {
      setDate(siege.date ?? "");
    }
  }, [siege]);

  // Eagerly run validation on mount so the Notify button is correctly gated
  // even if the user hasn't manually clicked "Validate" in this session.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    validateMutation.mutate();
  }, [siegeId]);

  const updateMutation = useMutation({
    mutationFn: () =>
      updateSiege(siegeId, {
        date: date || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["siege", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["sieges"] });
      setSettingsError("");
    },
    onError: (err) => {
      setSettingsError(
        isAxiosError(err)
          ? (err.response?.data?.detail ?? "Save failed")
          : "Save failed"
      );
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteSiege(siegeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sieges"] });
      navigate("/sieges");
    },
  });

  const activateMutation = useMutation({
    mutationFn: () => activateSiege(siegeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["siege", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["sieges"] });
      setActivateErrors([]);
    },
    onError: (err) => {
      if (isAxiosError(err) && err.response?.status === 400) {
        const detail = err.response.data?.detail;
        if (
          Array.isArray(detail) &&
          detail.length > 0 &&
          typeof detail[0].rule === "number"
        ) {
          setActivateErrors(detail as ValidationIssue[]);
        } else {
          setActivateErrors([
            {
              rule: 0,
              message:
                typeof detail === "string" ? detail : "Activation failed",
              context: null,
            },
          ]);
        }
      } else {
        setActivateErrors([
          { rule: 0, message: "Activation failed", context: null },
        ]);
      }
    },
  });

  const completeMutation = useMutation({
    mutationFn: () => completeSiege(siegeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["siege", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["sieges"] });
    },
  });

  const cloneMutation = useMutation({
    mutationFn: () => cloneSiege(siegeId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["sieges"] });
      navigate(`/sieges/${data.id}`);
    },
  });

  const reopenMutation = useMutation({
    mutationFn: () => reopenSiege(siegeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["siege", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["sieges"] });
    },
  });

  const validateMutation = useMutation({
    mutationFn: () => validateSiege(siegeId),
    onSuccess: (data) => setValidation(data),
  });

  const updateBuildingMutation = useMutation({
    mutationFn: ({
      buildingId,
      data,
    }: {
      buildingId: number;
      data: { level?: number; is_broken?: boolean };
    }) => updateBuilding(siegeId, buildingId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["buildings", siegeId] });
      queryClient.invalidateQueries({ queryKey: ["siege", siegeId] });
    },
  });

  const notifyMutation = useMutation({
    mutationFn: () => notifySiegeMembers(siegeId),
    onSuccess: (data) => {
      setNotifyBatch(data);
      setNotifyConfirmOpen(false);
      setNotifyError(null);
    },
    onError: (err) => {
      setNotifyError(
        isAxiosError(err)
          ? (err.response?.data?.detail ?? "Failed to send notifications.")
          : "Failed to send notifications."
      );
      setNotifyConfirmOpen(false);
    },
  });

  const postChannelMutation = useMutation({
    mutationFn: () => postToChannel(siegeId),
    onSuccess: (data) => {
      setPostChannelResult(
        data.status === "ok"
          ? "Posted successfully."
          : (data.detail ?? "Posted.")
      );
      setPostChannelError(null);
      setPostConfirmOpen(false);
    },
    onError: (err) => {
      setPostChannelError(
        isAxiosError(err)
          ? (err.response?.data?.detail ?? "Post failed")
          : "Post failed"
      );
      setPostChannelResult(null);
      setPostConfirmOpen(false);
    },
  });

  const generateImagesMutation = useMutation({
    mutationFn: () => generateImages(siegeId),
    onSuccess: (data) => setGeneratedImages(data),
  });

  function downloadImage(base64: string, filename: string) {
    const byteString = atob(base64);
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) {
      ia[i] = byteString.charCodeAt(i);
    }
    const blob = new Blob([ab], { type: "image/png" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (siegeLoading) {
    return <div className="py-12 text-center text-slate-500">Loading...</div>;
  }

  return (
    <div className="max-w-3xl">
      <Link
        to="/sieges"
        className="mb-6 flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Sieges
      </Link>

      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">
          Siege {siege?.date ?? `#${siegeId}`}
        </h1>
      </div>

      {/* Lifecycle */}
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-6">
        <div className="mb-4 flex items-center gap-3">
          <h2 className="text-base font-semibold text-slate-900">Lifecycle</h2>
          {siege?.status === "planning" && (
            <Badge variant="secondary">Planning</Badge>
          )}
          {siege?.status === "active" && (
            <Badge className="bg-green-100 text-green-800 hover:bg-green-100">
              Active
            </Badge>
          )}
          {siege?.status === "complete" && (
            <Badge variant="outline">Complete</Badge>
          )}
        </div>
        <div className="flex flex-row flex-wrap items-center gap-3">
          {siege?.status === "planning" && (
            <Button
              variant="default"
              onClick={() => activateMutation.mutate()}
              disabled={activateMutation.isPending}
            >
              Start Siege
            </Button>
          )}
          {siege?.status === "active" && (
            <Button
              variant="secondary"
              onClick={() => completeMutation.mutate()}
              disabled={completeMutation.isPending}
            >
              Close Siege
            </Button>
          )}
          {siege?.status === "complete" && (
            <Button
              variant="outline"
              onClick={() => reopenMutation.mutate()}
              disabled={reopenMutation.isPending}
            >
              Reopen Siege
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => setCloneConfirmOpen(true)}
            disabled={cloneMutation.isPending}
          >
            Clone
          </Button>
          {siege?.status === "planning" && (
            <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
              Delete
            </Button>
          )}
        </div>
        {activateErrors.length > 0 && (
          <div className="mt-4 space-y-2">
            {activateErrors.map((e, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-md bg-red-50 px-3 py-2"
              >
                {e.rule > 0 && (
                  <Badge variant="destructive" className="mt-0.5 shrink-0">
                    Error {e.rule}
                  </Badge>
                )}
                <p className="text-sm text-red-700">{e.message}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Discord Notifications */}
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="mb-4 text-base font-semibold text-slate-900">
          Discord Notifications
        </h2>

        {/* Action buttons in a horizontal row */}
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setNotifyConfirmOpen(true)}
            disabled={
              notifyMutation.isPending ||
              batchInProgress ||
              siege?.status === "complete" ||
              !siege?.date ||
              (validation !== null && validation.errors.length > 0)
            }
            title={
              !siege?.date
                ? "Set a siege date before sending to Discord"
                : undefined
            }
          >
            {notifyMutation.isPending || batchInProgress ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Send className="mr-1.5 h-4 w-4" />
            )}
            Notify Members
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPostConfirmOpen(true)}
            disabled={
              postChannelMutation.isPending ||
              siege?.status === "complete" ||
              !siege?.date
            }
            title={
              !siege?.date
                ? "Set a siege date before sending to Discord"
                : undefined
            }
          >
            {postChannelMutation.isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <MessageSquare className="mr-1.5 h-4 w-4" />
            )}
            Post to Discord
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => generateImagesMutation.mutate()}
            disabled={
              generateImagesMutation.isPending || siege?.status === "complete"
            }
          >
            {generateImagesMutation.isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Image className="mr-1.5 h-4 w-4" />
            )}
            Generate Images
          </Button>
        </div>

        {!siege?.date && (
          <p className="flex items-center gap-1.5 text-sm text-amber-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Set a siege date before sending notifications or posting to Discord.
          </p>
        )}

        {validation !== null && validation.errors.length > 0 && (
          <p className="flex items-center gap-1.5 text-sm text-red-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Notifications blocked: resolve {validation.errors.length} validation
            error
            {validation.errors.length !== 1 ? "s" : ""} before notifying
            members.
          </p>
        )}

        {/* Results / status areas */}
        <div className="space-y-4">
          {/* Notify error (e.g. 400 from validation guard) */}
          {notifyError && (
            <p className="flex items-center gap-1 text-sm text-red-600">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {notifyError}
            </p>
          )}
          {/* Notify Members batch results */}
          {notifyBatch && (
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <p className="mb-2 text-xs text-slate-500">
                Batch #{notifyBatch.batch_id} &bull; {notifyBatch.member_count}{" "}
                members &bull;{" "}
                <span
                  className={cn(
                    "font-medium",
                    batchData?.status === "completed"
                      ? "text-green-600"
                      : "text-amber-600"
                  )}
                >
                  {batchData?.status ?? notifyBatch.status}
                </span>
              </p>
              <ul className="space-y-1">
                {(batchData?.results ?? []).map((item) => (
                  <li
                    key={item.member_id}
                    className="flex items-center gap-2 text-sm"
                  >
                    {item.success === true && (
                      <Check className="h-4 w-4 shrink-0 text-green-600" />
                    )}
                    {item.success === false && (
                      <X className="h-4 w-4 shrink-0 text-red-600" />
                    )}
                    {item.discord_username === null &&
                      item.success === null && (
                        <AlertCircle className="h-4 w-4 shrink-0 text-yellow-500" />
                      )}
                    {item.discord_username !== null &&
                      item.success === null &&
                      !batchComplete && (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-slate-400" />
                      )}
                    {item.discord_username !== null &&
                      item.success === null &&
                      batchComplete && (
                        <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
                      )}
                    <span
                      className={cn(
                        item.success === true && "text-slate-700",
                        item.success === false && "text-red-700",
                        item.discord_username === null &&
                          item.success === null &&
                          "text-yellow-700",
                        item.discord_username !== null &&
                          item.success === null &&
                          !batchComplete &&
                          "text-slate-500",
                        item.discord_username !== null &&
                          item.success === null &&
                          batchComplete &&
                          "text-red-700"
                      )}
                    >
                      {item.member_name}
                      {item.discord_username === null && item.success === null
                        ? " — No Discord username"
                        : null}
                      {item.success === false && item.error
                        ? ` — ${item.error}`
                        : null}
                      {item.discord_username !== null &&
                      item.success === null &&
                      batchComplete
                        ? " — Status unknown"
                        : null}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Post to Discord status */}
          {postChannelResult && (
            <p className="text-sm text-green-600">{postChannelResult}</p>
          )}
          {postChannelError && (
            <p className="text-sm text-red-600">{postChannelError}</p>
          )}

          {/* Generate Images error */}
          {generateImagesMutation.isError && (
            <p className="text-sm text-red-600">
              {isAxiosError(generateImagesMutation.error)
                ? (generateImagesMutation.error.response?.data?.detail ??
                  "Generation failed")
                : "Generation failed"}
            </p>
          )}

          {/* Generated image previews */}
          {generatedImages && (
            <div className="space-y-4">
              <div>
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-700">
                    Assignments
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() =>
                      downloadImage(
                        generatedImages.assignments_image,
                        `siege-${siegeId}-assignments.png`
                      )
                    }
                  >
                    <Download className="mr-1 h-3 w-3" />
                    Download
                  </Button>
                </div>
                <img
                  src={`data:image/png;base64,${generatedImages.assignments_image}`}
                  alt="Assignments"
                  className="max-w-full rounded-md border border-slate-200"
                />
              </div>
              <div>
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-700">
                    Reserves
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() =>
                      downloadImage(
                        generatedImages.reserves_image,
                        `siege-${siegeId}-reserves.png`
                      )
                    }
                  >
                    <Download className="mr-1 h-3 w-3" />
                    Download
                  </Button>
                </div>
                <img
                  src={`data:image/png;base64,${generatedImages.reserves_image}`}
                  alt="Reserves"
                  className="max-w-full rounded-md border border-slate-200"
                />
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Siege Settings */}
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="mb-4 text-base font-semibold text-slate-900">
          Siege Settings
        </h2>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="date">Date</Label>
            <Input
              id="date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              disabled={siegeLocked}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Defense Scroll Count</Label>
            <div className="flex items-baseline gap-4">
              <p className="text-sm font-medium text-slate-900">
                {siege?.computed_scroll_count ?? 0}
              </p>
              {siegeMembers !== undefined && siegeMembers.length > 0 && (
                <p className="text-sm text-slate-500">
                  Scrolls per member:{" "}
                  <span className="font-medium text-slate-900">
                    {(siege?.computed_scroll_count ?? 0) < 90 ? 3 : 4}
                  </span>
                </p>
              )}
            </div>
          </div>
          {settingsError && (
            <p className="text-sm text-red-600">{settingsError}</p>
          )}
          <Button
            onClick={() => updateMutation.mutate()}
            disabled={updateMutation.isPending || siegeLocked}
          >
            {updateMutation.isPending ? "Saving..." : "Save Settings"}
          </Button>
          {updateMutation.isSuccess && (
            <span className="ml-3 text-sm text-green-600">Saved.</span>
          )}
        </div>
      </section>

      {/* Buildings */}
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="mb-4 text-base font-semibold text-slate-900">
          Buildings
        </h2>
        {buildings && buildings.length > 0 && (
          <div className="mb-4 space-y-2">
            {buildings
              .filter((b) => b.building_type !== "post")
              .map((b) => {
                const colors = BUILDING_COLORS[b.building_type];
                return (
                  <div
                    key={b.id}
                    className={cn(
                      "flex overflow-hidden rounded-md border",
                      colors.border,
                      b.is_broken && "opacity-60"
                    )}
                  >
                    {/* Colored label bar — mirrors the Board page building header */}
                    <div
                      className={cn(
                        colors.header,
                        "flex w-36 shrink-0 flex-col justify-center px-2 py-2 text-white"
                      )}
                    >
                      <p className="text-xs font-semibold leading-tight">
                        {BUILDING_LABELS[b.building_type]} {b.building_number}
                      </p>
                      <p className="mt-0.5 text-xs opacity-75">
                        Lv {b.level}
                        {b.is_broken ? " · Broken" : ""}
                      </p>
                    </div>

                    {/* Controls area */}
                    <div
                      className={cn(
                        "flex flex-1 items-center gap-4 px-3 py-2",
                        colors.bg
                      )}
                    >
                      <div className="flex items-center gap-1">
                        <span className="mr-1 text-xs text-slate-500">Lvl</span>
                        {[1, 2, 3, 4, 5, 6].map((lvl) => (
                          <button
                            key={lvl}
                            className={cn(
                              "h-7 w-7 rounded text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                              b.level === lvl
                                ? cn(colors.header, "text-white")
                                : "bg-white text-slate-600 hover:bg-slate-100"
                            )}
                            disabled={siegeLocked}
                            onClick={() => {
                              if (lvl !== b.level) {
                                updateBuildingMutation.mutate({
                                  buildingId: b.id,
                                  data: { level: lvl },
                                });
                              }
                            }}
                          >
                            {lvl}
                          </button>
                        ))}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Checkbox
                          id={`broken-${b.id}`}
                          checked={b.is_broken}
                          disabled={siegeLocked}
                          onCheckedChange={
                            siegeLocked
                              ? undefined
                              : (v) =>
                                  updateBuildingMutation.mutate({
                                    buildingId: b.id,
                                    data: { is_broken: Boolean(v) },
                                  })
                          }
                        />
                        <Label
                          htmlFor={`broken-${b.id}`}
                          className="text-xs text-slate-500"
                        >
                          Broken
                        </Label>
                      </div>
                    </div>
                  </div>
                );
              })}
          </div>
        )}
      </section>

      {/* Validation */}
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">Validation</h2>
          <Button
            variant="outline"
            size="sm"
            onClick={() => validateMutation.mutate()}
            disabled={validateMutation.isPending}
          >
            {validateMutation.isPending ? "Checking..." : "Run Validation"}
          </Button>
        </div>
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
      </section>

      {/* Delete siege dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Siege</DialogTitle>
            <DialogDescription>
              This action cannot be undone. All buildings and assignments will
              be permanently deleted.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Notify Members confirm dialog */}
      <Dialog open={notifyConfirmOpen} onOpenChange={setNotifyConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Notify Members</DialogTitle>
            <DialogDescription>
              Send Discord DMs to all members with their siege assignments. This
              cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setNotifyConfirmOpen(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={() => notifyMutation.mutate()}
              disabled={notifyMutation.isPending}
            >
              Send Notifications
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Clone siege confirm dialog */}
      <Dialog open={cloneConfirmOpen} onOpenChange={setCloneConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Clone Siege</DialogTitle>
            <DialogDescription>
              Are you sure you want to clone this siege?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCloneConfirmOpen(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                setCloneConfirmOpen(false);
                cloneMutation.mutate();
              }}
              disabled={cloneMutation.isPending}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Post to Discord confirm dialog */}
      <Dialog open={postConfirmOpen} onOpenChange={setPostConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Post to Discord</DialogTitle>
            <DialogDescription>
              Post the siege assignment images to the configured Discord
              channel.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPostConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => postChannelMutation.mutate()}
              disabled={postChannelMutation.isPending}
            >
              Post
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
