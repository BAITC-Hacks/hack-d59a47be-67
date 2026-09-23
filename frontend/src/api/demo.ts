import { ApiError, type CareerClient } from "./client";
import {
  DEMO_DATA_VERSION,
  DEMO_EMPLOYEE_ID,
  DEMO_EVENT_IDS,
  DEMO_SCENARIO_DATE,
  demoCatalog,
  demoEffects,
  demoHistory,
  demoProfiles,
  demoRecommendations,
  demoSnapshots,
  otherDemoDetails,
} from "./fixtures";
import type {
  CompletionRequest,
  CompletionResponse,
  EmployeeDetailResponse,
  RecommendationResponse,
  UserIdentity,
} from "./types";

export { DEMO_EMPLOYEE_ID, DEMO_SCENARIO_DATE } from "./fixtures";

const STORAGE_KEY = "career-quest.synthetic-demo.v1";
const clone = <T>(value: T): T => structuredClone(value);
type Receipt = { request: string; response: CompletionResponse };
interface DemoState {
  version: 1;
  completed: string[];
  receipts: Record<string, Receipt>;
  latest: Record<string, RecommendationResponse>;
}
const initialState = (): DemoState => ({
  version: 1,
  completed: [],
  receipts: {},
  latest: {},
});
let memory: DemoState | null = null;

function state(): DemoState {
  if (memory) return memory;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object") {
      const saved = parsed as DemoState;
      if (
        saved.version === 1 &&
        Array.isArray(saved.completed) &&
        saved.completed.every((id) =>
          DEMO_EVENT_IDS.some((known) => known === id),
        ) &&
        new Set(saved.completed).size === saved.completed.length &&
        saved.receipts &&
        typeof saved.receipts === "object" &&
        saved.latest &&
        typeof saved.latest === "object"
      ) {
        // Only this clearly marked synthetic store is ever read or persisted.
        memory = saved;
        return saved;
      }
    }
  } catch {
    // Unavailable/corrupt browser storage starts a fresh in-memory demonstration.
  }
  memory = initialState();
  return memory;
}

function save(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state()));
  } catch {
    // The demonstration remains usable for the current page session.
  }
}

export function resetDemo(): void {
  memory = initialState();
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage may be disabled; resetting the in-memory scenario is sufficient.
  }
}

function snapshot(completed = state().completed) {
  const prepared = demoSnapshots.find(
    (item) =>
      item.completed.length === completed.length &&
      item.completed.every((id) => completed.includes(id)),
  );
  if (!prepared)
    throw new ApiError(
      409,
      "DEMO_UNSUPPORTED",
      "Для этого действия нет подготовленного демоответа. Начните демо заново.",
    );
  return prepared;
}

function versionCheck(expected: number): void {
  if (expected !== state().completed.length) {
    throw new ApiError(
      409,
      "REVISION_CONFLICT",
      "Данные изменились. Обновите профиль и повторите действие.",
    );
  }
}

function demoOnly(
  message = "Это действие доступно после подключения к серверу. В демо показан подготовленный сценарий развития Алии.",
): never {
  throw new ApiError(422, "DEMO_UNSUPPORTED", message);
}

function primaryDetail(): EmployeeDetailResponse {
  const current = state();
  const prepared = snapshot();
  const receipts = Object.values(current.receipts).map((item) => item.response);
  return {
    data_version: `${DEMO_DATA_VERSION}:${current.completed.length}`,
    state_version: current.completed.length,
    profile: clone(demoProfiles[0]),
    goal: { target_role: "Backend Engineer", target_grade: "Senior" },
    current_skills: clone(prepared.current_skills),
    gaps: clone(prepared.gaps),
    history: [
      ...clone(demoHistory),
      ...receipts.map(({ completion }) => ({
        record_id: completion.completion_id,
        event_id: completion.event_id,
        status: "completed" as const,
        activity_date: DEMO_SCENARIO_DATE,
        date_source: "completed_at" as const,
        completed_at: completion.completed_at,
        mode: completion.mode,
        effects: clone(completion.effects),
      })),
    ],
    scenario_date: DEMO_SCENARIO_DATE,
    target_status: "explicit",
    progress: clone(prepared.progress),
  };
}

/** Canned UI responses, selected deliberately by the user. Never handles real data. */
export function createDemoClient(
  role: UserIdentity["role"] = "employee",
): CareerClient {
  const user: UserIdentity =
    role === "hr"
      ? {
          id: "DEMO_USER_HR",
          username: "demo-hr",
          role: "hr",
          employee_id: null,
        }
      : {
          id: "DEMO_USER_ALIYA",
          username: "demo-employee",
          role: "employee",
          employee_id: DEMO_EMPLOYEE_ID,
        };
  let signedIn = true;

  function authenticated(): void {
    if (!signedIn)
      throw new ApiError(
        401,
        "UNAUTHENTICATED",
        "Демо завершено. Откройте его заново.",
      );
  }
  function hrOnly(): void {
    authenticated();
    if (role !== "hr")
      throw new ApiError(
        403,
        "FORBIDDEN",
        "Откройте демо HR, чтобы посмотреть команду.",
      );
  }
  function readable(id: string): void {
    authenticated();
    if (role === "employee" && id !== DEMO_EMPLOYEE_ID) {
      throw new ApiError(
        403,
        "FORBIDDEN",
        "Этот профиль недоступен в демо сотрудника.",
      );
    }
    if (!demoProfiles.some((profile) => profile.employee_id === id)) {
      throw new ApiError(404, "EMPLOYEE_NOT_FOUND", "Демо-профиль не найден.");
    }
  }
  function supported(id: string): void {
    readable(id);
    if (id !== DEMO_EMPLOYEE_ID) demoOnly();
  }

  return {
    async login() {
      signedIn = true;
      return {
        user: clone(user),
        csrf_token: "synthetic-demo-no-server-session",
        expires_at: "2099-01-01T00:00:00Z",
      };
    },
    async me() {
      authenticated();
      return clone(user);
    },
    async logout() {
      signedIn = false;
    },
    async health() {
      return {
        status: "ok",
        capabilities: {
          ai: { status: "not_configured", engine: "none" },
          dataset: { status: "loaded", version: DEMO_DATA_VERSION },
        },
      };
    },
    async catalog() {
      authenticated();
      return clone(demoCatalog);
    },
    async employee(id) {
      readable(id);
      if (id === DEMO_EMPLOYEE_ID) return primaryDetail();
      return {
        ...clone(otherDemoDetails[id]),
        state_version: state().completed.length,
      };
    },
    async employees(limit = 20, offset = 0) {
      hrOnly();
      return {
        items: clone(demoProfiles.slice(offset, offset + limit)),
        total: demoProfiles.length,
        limit,
        offset,
      };
    },
    async recommendations(id, scenarioDate) {
      readable(id);
      if (scenarioDate !== DEMO_SCENARIO_DATE)
        demoOnly("Демо подготовлено на 1 октября 2026 года. Обновите профиль.");
      const recommendations =
        id === DEMO_EMPLOYEE_ID
          ? demoRecommendations.filter(
              (item) => !state().completed.includes(item.event_id),
            )
          : [];
      const status =
        id === "DEMO_EMPLOYEE_AIDANA"
          ? "no_target"
          : recommendations.length
            ? "ok"
            : "no_candidates";
      const response: RecommendationResponse = {
        data_version: `${DEMO_DATA_VERSION}:${state().completed.length}`,
        state_version: state().completed.length,
        scenario_date: DEMO_SCENARIO_DATE,
        // This field follows the transport fixture; the UI must label demo output
        // as prepared examples. health() never claims a model is configured.
        engine: recommendations.length ? "oleg" : "none",
        status,
        recommendation_id: `DEMO_RECOMMENDATION_${id}_${state().completed.length}`,
        stale: false,
        recommendations: clone(recommendations),
      };
      state().latest[id] = clone(response);
      save();
      return response;
    },
    async latest(id) {
      readable(id);
      const latest = state().latest[id];
      if (!latest)
        throw new ApiError(
          404,
          "RECOMMENDATION_NOT_FOUND",
          "Рекомендаций ещё нет. Подберите следующий шаг.",
        );
      return {
        ...clone(latest),
        stale: latest.state_version !== state().completed.length,
      };
    },
    async preview(id, body) {
      supported(id);
      versionCheck(body.expected_state_version);
      if (body.scenario_date !== DEMO_SCENARIO_DATE)
        demoOnly("Обновите профиль: дата демосценария — 1 октября 2026 года.");
      if (
        !body.event_ids.length ||
        body.event_ids.length > 3 ||
        new Set(body.event_ids).size !== body.event_ids.length
      )
        demoOnly();
      if (
        body.event_ids.some(
          (eventId) =>
            !DEMO_EVENT_IDS.some((known) => known === eventId) ||
            state().completed.includes(eventId),
        )
      ) {
        throw new ApiError(
          409,
          "EVENT_NOT_ELIGIBLE",
          "Выберите незавершённый шаг из рекомендаций.",
        );
      }
      const prepared = snapshot([...state().completed, ...body.event_ids]);
      return {
        data_version: `${DEMO_DATA_VERSION}:${state().completed.length}`,
        state_version: state().completed.length,
        scenario_date: DEMO_SCENARIO_DATE,
        persisted: false,
        effects: clone(
          body.event_ids.flatMap((eventId) => demoEffects[eventId]),
        ),
        projected_skills: clone(prepared.current_skills),
        projected_gaps: clone(prepared.gaps),
      };
    },
    async complete(id, body, key) {
      supported(id);
      if (!key || key.length > 128)
        throw new ApiError(
          422,
          "IDEMPOTENCY_KEY_REQUIRED",
          "Не удалось определить операцию. Повторите действие.",
        );
      const request = requestSignature(id, body);
      const receiptKey = `key:${key}`;
      const cached = Object.hasOwn(state().receipts, receiptKey)
        ? state().receipts[receiptKey]
        : undefined;
      if (cached) {
        if (cached.request !== request)
          throw new ApiError(
            409,
            "IDEMPOTENCY_CONFLICT",
            "Эта операция уже отправлялась с другими данными.",
          );
        return clone(cached.response);
      }
      versionCheck(body.expected_state_version);
      if (
        !DEMO_EVENT_IDS.some((eventId) => eventId === body.event_id) ||
        body.record_id
      )
        demoOnly();
      if (state().completed.includes(body.event_id))
        throw new ApiError(
          409,
          "ALREADY_COMPLETED",
          "Шаг уже завершён. Обновите профиль.",
        );
      if (body.mode !== "completion" && body.mode !== "demo_simulation")
        demoOnly();
      const response: CompletionResponse = {
        state_version: state().completed.length + 1,
        completion: {
          completion_id: `DEMO_COMPLETION_${body.event_id}`,
          event_id: body.event_id,
          mode: body.mode,
          completed_at: "2026-10-01T12:00:00+05:00",
          effects: clone(demoEffects[body.event_id]),
        },
      };
      state().completed.push(body.event_id);
      state().receipts[receiptKey] = { request, response: clone(response) };
      save();
      return response;
    },
    async goal(id, body) {
      supported(id);
      versionCheck(body.expected_state_version);
      if (
        body.goal.target_role !== "Backend Engineer" ||
        body.goal.target_grade !== "Senior"
      ) {
        demoOnly(
          "В этом демо подготовлена цель Backend Engineer · Senior. Другую цель можно выбрать после подключения к серверу.",
        );
      }
      return primaryDetail();
    },
    async hrSummary() {
      hrOnly();
      const receipts = Object.values(state().receipts);
      return {
        data_version: `${DEMO_DATA_VERSION}:${state().completed.length}`,
        employee_count: 4,
        employees_with_goal: 3,
        completion_count: 1 + receipts.length,
        demo_simulation_count: receipts.filter(
          (item) => item.response.completion.mode === "demo_simulation",
        ).length,
      };
    },
    async importFiles() {
      hrOnly();
      demoOnly(
        "Импорт доступен после подключения к серверу. Демо не принимает и не сохраняет ваши файлы.",
      );
    },
  };
}

function requestSignature(id: string, body: CompletionRequest): string {
  return JSON.stringify([
    id,
    body.expected_state_version,
    body.event_id,
    body.mode,
    body.record_id ?? null,
  ]);
}
