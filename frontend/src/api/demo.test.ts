import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createDemoClient,
  DEMO_EMPLOYEE_ID,
  DEMO_SCENARIO_DATE,
  resetDemo,
} from "./demo";
import { DEMO_EVENT_IDS } from "./fixtures";
import type { CompletionRequest } from "./types";

beforeEach(resetDemo);

describe("explicit synthetic demonstration", () => {
  it("previews without mutation and shows a prepared before/after on completion", async () => {
    const client = createDemoClient();
    const before = await client.employee(DEMO_EMPLOYEE_ID);
    const preview = await client.preview(DEMO_EMPLOYEE_ID, {
      expected_state_version: before.state_version,
      scenario_date: DEMO_SCENARIO_DATE,
      event_ids: ["DEMO_SYSTEMS"],
    });
    expect(preview.persisted).toBe(false);
    expect((await client.employee(DEMO_EMPLOYEE_ID)).progress?.percent).toBe(
      72.73,
    );
    const result = await client.complete(
      DEMO_EMPLOYEE_ID,
      {
        expected_state_version: 0,
        event_id: "DEMO_SYSTEMS",
        mode: "demo_simulation",
      },
      "one",
    );
    const after = await client.employee(DEMO_EMPLOYEE_ID);
    expect(after.progress?.percent).toBe(86.36);
    expect(after.state_version).toBe(result.state_version);
    expect(after.current_skills).toEqual(preview.projected_skills);
    expect(after.history.at(-1)?.mode).toBe("demo_simulation");
  });

  it("replays the exact result before revision validation and rejects changed bodies", async () => {
    const client = createDemoClient();
    const body: CompletionRequest = {
      expected_state_version: 0,
      event_id: "DEMO_SYSTEMS",
      mode: "completion",
    };
    const first = await client.complete(DEMO_EMPLOYEE_ID, body, "saved-key");
    const reloadedClient = createDemoClient();
    expect(
      await reloadedClient.complete(DEMO_EMPLOYEE_ID, body, "saved-key"),
    ).toEqual(first);
    expect(
      (await reloadedClient.employee(DEMO_EMPLOYEE_ID)).history,
    ).toHaveLength(2);
    await expect(
      client.complete(
        DEMO_EMPLOYEE_ID,
        { ...body, expected_state_version: 1 },
        "saved-key",
      ),
    ).rejects.toMatchObject({ code: "IDEMPOTENCY_CONFLICT" });
  });

  it("marks saved recommendations stale and returns remaining prepared steps after refresh", async () => {
    const client = createDemoClient();
    const recommendations = await client.recommendations(
      DEMO_EMPLOYEE_ID,
      DEMO_SCENARIO_DATE,
    );
    expect(recommendations.recommendations).toHaveLength(3);
    recommendations.recommendations.forEach((item) =>
      expect(item.explanation.evidence_ids.length).toBeGreaterThanOrEqual(3),
    );
    await client.complete(
      DEMO_EMPLOYEE_ID,
      {
        expected_state_version: 0,
        event_id: "DEMO_PYTHON",
        mode: "completion",
      },
      "one",
    );
    expect((await client.latest(DEMO_EMPLOYEE_ID)).stale).toBe(true);
    const fresh = await client.recommendations(
      DEMO_EMPLOYEE_ID,
      DEMO_SCENARIO_DATE,
    );
    expect(fresh.stale).toBe(false);
    expect(fresh.recommendations.map((item) => item.event_id)).not.toContain(
      "DEMO_PYTHON",
    );
  });

  it("has prepared snapshots for every order of the three steps", async () => {
    const orders = [
      [0, 1, 2],
      [0, 2, 1],
      [1, 0, 2],
      [1, 2, 0],
      [2, 0, 1],
      [2, 1, 0],
    ];
    for (const order of orders) {
      resetDemo();
      const client = createDemoClient();
      for (const [revision, eventIndex] of order.entries()) {
        await client.complete(
          DEMO_EMPLOYEE_ID,
          {
            expected_state_version: revision,
            event_id: DEMO_EVENT_IDS[eventIndex],
            mode: "completion",
          },
          `step-${revision}`,
        );
      }
      expect((await client.employee(DEMO_EMPLOYEE_ID)).progress?.percent).toBe(
        100,
      );
      expect(
        (await client.recommendations(DEMO_EMPLOYEE_ID, DEMO_SCENARIO_DATE))
          .status,
      ).toBe("no_candidates");
    }
  });

  it("does not accept imported content and enforces the selected demo role", async () => {
    const employee = createDemoClient();
    await expect(employee.hrSummary()).rejects.toMatchObject({ status: 403 });
    await expect(
      employee.employee("DEMO_EMPLOYEE_AIDANA"),
    ).rejects.toMatchObject({ status: 403 });
    const hr = createDemoClient("hr");
    await expect(
      hr.importFiles({
        dry_run: true,
        files: [
          {
            source_filename: "employees.json",
            source_format: "json",
            content: "never-store-this",
          },
        ],
      }),
    ).rejects.toMatchObject({ code: "DEMO_UNSUPPORTED" });
    expect(JSON.stringify(localStorage)).not.toContain("never-store-this");
    expect((await hr.hrSummary()).employee_count).toBe(4);
    expect(
      (await hr.recommendations("DEMO_EMPLOYEE_AIDANA", DEMO_SCENARIO_DATE))
        .status,
    ).toBe("no_target");
  });

  it("counts simulation completions as a subset of all completions, matching backend semantics", async () => {
    const hr = createDemoClient("hr");
    await hr.complete(
      DEMO_EMPLOYEE_ID,
      {
        expected_state_version: 0,
        event_id: "DEMO_SYSTEMS",
        mode: "demo_simulation",
      },
      "__proto__",
    );
    expect(await hr.hrSummary()).toMatchObject({
      completion_count: 2,
      demo_simulation_count: 1,
    });
    resetDemo();
    expect((await hr.employee(DEMO_EMPLOYEE_ID)).progress?.percent).toBe(72.73);
  });

  it("restores only synthetic snapshots and receipts after a module reload", async () => {
    const client = createDemoClient();
    const body: CompletionRequest = {
      expected_state_version: 0,
      event_id: "DEMO_COMMUNICATION",
      mode: "completion",
    };
    const saved = await client.complete(
      DEMO_EMPLOYEE_ID,
      body,
      "persisted-key",
    );
    vi.resetModules();
    const reloadedModule = await import("./demo");
    const reloaded = reloadedModule.createDemoClient();
    expect((await reloaded.employee(DEMO_EMPLOYEE_ID)).progress?.percent).toBe(
      77.27,
    );
    expect(
      await reloaded.complete(DEMO_EMPLOYEE_ID, body, "persisted-key"),
    ).toEqual(saved);
    reloadedModule.resetDemo();
  });
});
