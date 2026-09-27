import { describe, expect, it } from "vitest";
import { isSiegeLocked } from "../../lib/siege";

describe("isSiegeLocked", () => {
  it("fails closed until planning status is known", () => {
    expect(isSiegeLocked(undefined)).toBe(true);
    expect(isSiegeLocked(null)).toBe(true);
    expect(isSiegeLocked({ status: "planning" })).toBe(false);
    expect(isSiegeLocked({ status: "active" })).toBe(true);
    expect(isSiegeLocked({ status: "complete" })).toBe(true);
  });
});
