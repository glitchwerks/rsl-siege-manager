import type { Siege } from "../api/types";

export function isSiegeLocked(
  siege: Pick<Siege, "status"> | null | undefined
): boolean {
  return siege == null || siege.status !== "planning";
}
