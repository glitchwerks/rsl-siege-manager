import { describe, expect, it } from "vitest";
import { isSiegeLocked } from "../../lib/siege";

describe("isSiegeLocked", () => {
  it("only locks sieges that have left planning", () => {
    expect(isSiegeLocked(undefined)).toBe(false);
    expect(isSiegeLocked({ status: "planning" })).toBe(false);
    expect(isSiegeLocked({ status: "active" })).toBe(true);
    expect(isSiegeLocked({ status: "complete" })).toBe(true);
  });
});
