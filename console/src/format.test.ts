import { describe, expect, it } from "vitest";
import { checkName, cost, duration, outcome, rungMark, rupees, words } from "./format";
import { parseRoute } from "./hooks";

describe("money and time", () => {
  it("writes rupees the way the replies do", () => {
    expect(rupees(3499)).toBe("Rs 3,499");
    expect(rupees(1299.5)).toBe("Rs 1,299.50");
    expect(rupees("oops")).toBe("Rs ?");
  });

  it("never shows a tiny model cost as zero", () => {
    expect(cost(0)).toBe("Rs 0");
    expect(cost(0.004)).toBe("under Rs 0.01");
    expect(cost(0.1465)).toBe("Rs 0.15");
  });

  it("picks a unit that fits the wait", () => {
    expect(duration(null)).toBe("");
    expect(duration(420)).toBe("420 ms");
    expect(duration(2937)).toBe("2.9 s");
    expect(duration(133_000)).toBe("2 min 13 s");
    expect(duration(3 * 3_600_000 + 5 * 60_000)).toBe("3 h 5 min");
  });
});

describe("outcomes", () => {
  it("separates a hand off from a refusal", () => {
    expect(outcome({ status: "done", terminal_reason: "acted" })).toMatchObject({ label: "Acted", tone: "good" });
    expect(outcome({ status: "awaiting_approval", terminal_reason: null }).tone).toBe("wait");
    expect(outcome({ status: "done", terminal_reason: "escalated_message_signal" })).toMatchObject({ label: "Sent to a person", tone: "person" });
    const refused = outcome({ status: "done", terminal_reason: "escalated_guardrail_policy_rules" });
    expect(refused.tone).toBe("stop");
    expect(refused.why).toContain("the policy's numeric rules hold");
    expect(outcome({ status: "stopped", terminal_reason: null }).label).toBe("Stopped");
  });

  it("falls back to the code's own words for a reason it has not seen", () => {
    expect(outcome({ status: "done", terminal_reason: "escalated_something_new" }).why).toBe("something new");
    expect(checkName("a_new_check")).toBe("a new check");
    expect(words(null)).toBe("");
  });

  it("tells a check that passed from one that never ran", () => {
    expect(rungMark({ check: "allowlist", result: "passed" }).says).toBe("passed");
    expect(rungMark({ check: "policy_rules", result: "blocked" }).tone).toBe("stop");
    expect(rungMark({ check: "duplicate_refund", result: "not_reached" }).says).toBe("not reached");
    expect(rungMark({ check: "approval", result: "waits_for_a_person" }).tone).toBe("wait");
  });
});

describe("routes", () => {
  it("reads the screen from the address", () => {
    expect(parseRoute("")).toEqual({ screen: "inbox" });
    expect(parseRoute("#/tickets/gold_016")).toEqual({ screen: "ticket", id: "gold_016" });
    expect(parseRoute("#/tickets/T%3A1")).toEqual({ screen: "ticket", id: "T:1" });
    expect(parseRoute("#/approvals")).toEqual({ screen: "approvals" });
    expect(parseRoute("#/metrics")).toEqual({ screen: "metrics" });
    expect(parseRoute("#/nothing")).toEqual({ screen: "inbox" });
  });
});
