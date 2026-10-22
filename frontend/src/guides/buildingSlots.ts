import type { GuideTable } from "./types";

// Match backend/app/services/building_capacity.py and the board's groups of 3.
const capacities: { name: string; totals: number[] }[] = [
  { name: "Stronghold", totals: [12, 16, 18, 22, 25, 30] },
  { name: "Mana Shrine", totals: [6, 7, 9, 11, 13, 15] },
  { name: "Magic Tower", totals: [2, 3, 4, 5, 7, 9] },
  { name: "Defense Tower", totals: [2, 3, 4, 6, 9, 12] },
];

export const buildingSlotTables: GuideTable[] = capacities.map(
  ({ name, totals }) => {
    const groups = Math.ceil(Math.max(...totals) / 3);
    return {
      caption: `${name} — slots per group`,
      columns: [
        "Level",
        ...Array.from({ length: groups }, (_, index) => `Group ${index + 1}`),
        "Total slots",
      ],
      rows: totals.map((total, index) => [
        String(index + 1),
        ...Array.from({ length: groups }, (_, group) => {
          const slots = Math.min(3, total - group * 3);
          return slots > 0 ? String(slots) : "—";
        }),
        String(total),
      ]),
    };
  }
);
