import type {
  BatchImportRequest,
  CatalogResponse,
  CompletionRequest,
  CompletionResponse,
  EmployeeDetailResponse,
  EmployeeListResponse,
  GoalUpdateRequest,
  HealthResponse,
  HRSummaryResponse,
  ImportResponse,
  PreviewRequest,
  PreviewResponse,
  RecommendationResponse,
  SessionResponse,
  UserIdentity,
} from "./types";

export interface CareerClient {
  login(username: string, password: string): Promise<SessionResponse>;
  me(): Promise<UserIdentity>;
  logout(): Promise<void>;
  health(): Promise<HealthResponse>;
  catalog(): Promise<CatalogResponse>;
  employee(id: string): Promise<EmployeeDetailResponse>;
  employees(limit?: number, offset?: number): Promise<EmployeeListResponse>;
  recommendations(
    id: string,
    scenarioDate: string,
  ): Promise<RecommendationResponse>;
  latest(id: string): Promise<RecommendationResponse>;
  preview(id: string, body: PreviewRequest): Promise<PreviewResponse>;
  complete(
    id: string,
    body: CompletionRequest,
    key: string,
  ): Promise<CompletionResponse>;
  goal(id: string, body: GoalUpdateRequest): Promise<EmployeeDetailResponse>;
  hrSummary(): Promise<HRSummaryResponse>;
  importFiles(body: BatchImportRequest): Promise<ImportResponse>;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const CSRF_KEY = "career-quest.csrf.v1";
let memoryCsrf: string | null = null;
let sessionGeneration = 0;

export function getCsrf(): string | null {
  try {
    return sessionStorage.getItem(CSRF_KEY) ?? memoryCsrf;
  } catch {
    return memoryCsrf;
  }
}

function rememberCsrf(token: string): void {
  sessionGeneration++;
  memoryCsrf = token;
  try {
    sessionStorage.setItem(CSRF_KEY, token);
  } catch {
    // A session still works when browser storage is disabled; reload requires login.
  }
}

export function clearSession(): void {
  sessionGeneration++;
  memoryCsrf = null;
  try {
    sessionStorage.removeItem(CSRF_KEY);
  } catch {
    // No persistent token to remove in browsers with storage disabled.
  }
}

const messages: Record<string, string> = {
  INVALID_CREDENTIALS:
    "Неверный логин или пароль. Проверьте данные и попробуйте снова.",
  UNAUTHENTICATED: "Сессия завершилась. Войдите снова, чтобы продолжить.",
  FORBIDDEN: "У вашей учётной записи нет доступа к этому действию.",
  ORIGIN_FORBIDDEN:
    "Адрес приложения не разрешён сервером. Проверьте настройку подключения.",
  CSRF_FAILED: "Не удалось подтвердить сессию. Войдите снова.",
  CSRF_MISSING: "Войдите снова, чтобы безопасно сохранить изменения.",
  LOGIN_RATE_LIMITED: "Слишком много попыток входа. Попробуйте через 15 минут.",
  DATASET_NOT_LOADED:
    "Данные ещё не загружены. HR должен импортировать исходные файлы.",
  PROFILE_NOT_LOADED: "Профиль ещё не загружен. Обратитесь к HR.",
  EMPLOYEE_NOT_FOUND: "Профиль сотрудника не найден.",
  RECOMMENDATION_NOT_FOUND:
    "Сохранённых рекомендаций пока нет. Подберите следующий шаг.",
  REVISION_CONFLICT:
    "Данные изменились. Обновите профиль и повторите действие.",
  IDEMPOTENCY_CONFLICT:
    "Эта операция уже отправлялась с другими данными. Обновите профиль.",
  ALREADY_COMPLETED:
    "Активность уже завершена. Обновите профиль, чтобы увидеть результат.",
  EVENT_NOT_ELIGIBLE:
    "Активность больше не подходит текущему профилю. Обновите рекомендации.",
  INVALID_ACTIVITY_STATE:
    "Статус активности изменился. Обновите профиль перед завершением.",
  ACTIVE_PARTICIPATION:
    "Эта активность уже начата. Завершите её из истории назначений.",
  ASSIGNMENT_REQUIRED: "Обязательную активность нужно завершать из назначений.",
  SESSION_IN_FUTURE:
    "Событие ещё не наступило. Для просмотра результата используйте явную симуляцию.",
  NO_SESSION: "У мероприятия сейчас нет доступной сессии.",
  SESSION_ALREADY_COMPLETED: "Эта сессия уже завершена. Обновите профиль.",
  SCENARIO_DATE_MISMATCH: "Дата сценария изменилась. Обновите профиль.",
  VALIDATION_ERROR: "Проверьте заполненные поля: сервер отклонил запрос.",
  INVALID_SOURCE:
    "Файлы не прошли проверку. Исправьте указанные поля и проверьте их снова.",
  EMPLOYEE_CONFLICT:
    "Профиль с таким ID уже существует и отличается. Замена профилей пока не поддерживается.",
  HISTORY_CONFLICT:
    "Запись истории с таким ID уже существует с другими данными.",
  PREVIEW_REQUIRED: "Сначала проверьте файлы. Затем подтвердите импорт.",
  PREVIEW_MISMATCH:
    "Файлы изменились после проверки. Проверьте новую версию снова.",
  STALE_PREVIEW: "Данные изменились после проверки. Проверьте файлы ещё раз.",
  STORAGE_UNAVAILABLE:
    "Хранилище временно недоступно. Попробуйте ещё раз позже.",
  INTERNAL_ERROR: "Сервер не смог выполнить запрос. Попробуйте ещё раз.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorFromResponse(
  status: number,
  payload: unknown,
  requestId: string | null,
): ApiError {
  const value = isRecord(payload) ? payload : {};
  const code = typeof value.code === "string" ? value.code : "HTTP_ERROR";
  const details = isRecord(value.details) ? value.details : {};
  const fallback =
    status === 404
      ? "Запрошенные данные не найдены."
      : status >= 500
        ? "Сервер временно недоступен. Попробуйте ещё раз."
        : "Не удалось выполнить запрос. Проверьте данные и повторите действие.";
  return new ApiError(
    status,
    code,
    messages[code.toUpperCase()] ?? fallback,
    typeof value.request_id === "string"
      ? value.request_id
      : (requestId ?? undefined),
    details,
  );
}

interface ApiClientOptions {
  fetch?: typeof fetch;
  timeoutMs?: number;
}

/** Real requests only. Selecting synthetic demo data is an explicit UI decision. */
export function createApiClient(options: ApiClientOptions = {}): CareerClient {
  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? 15_000;

  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    extraHeaders: Record<string, string> = {},
    login = false,
  ): Promise<T> {
    // Requests belong to the session that started them. A delayed auth failure
    // must not invalidate a later successful login in the same browser tab.
    const requestGeneration = sessionGeneration;
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...extraHeaders,
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET" && !login) {
      const csrf = getCsrf();
      if (!csrf) throw new ApiError(401, "CSRF_MISSING", messages.CSRF_MISSING);
      headers["X-CSRF-Token"] = csrf;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetcher(`/api${path}`, {
        method,
        credentials: "include",
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: controller.signal,
        cache: "no-store",
      });
      const payload: unknown = await response.json().catch((error: unknown) => {
        if (controller.signal.aborted) throw error;
        return null;
      });
      if (!response.ok) {
        const error = errorFromResponse(
          response.status,
          payload,
          response.headers.get("X-Request-ID"),
        );
        if (
          requestGeneration === sessionGeneration &&
          (response.status === 401 || error.code === "CSRF_FAILED")
        )
          clearSession();
        throw error;
      }
      if (!isRecord(payload)) {
        throw new ApiError(
          response.status,
          "INVALID_RESPONSE",
          "Сервер вернул неожиданный ответ. Повторите запрос.",
        );
      }
      return payload as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (controller.signal.aborted) {
        throw new ApiError(
          0,
          "TIMEOUT",
          "Сервер отвечает дольше обычного. Повторите запрос; изменения могли сохраниться.",
        );
      }
      throw new ApiError(
        0,
        "NETWORK_ERROR",
        "Не удалось связаться с сервером. Проверьте подключение и повторите запрос.",
      );
    } finally {
      clearTimeout(timer);
    }
  }

  const employeePath = (id: string) => `/employees/${encodeURIComponent(id)}`;
  return {
    async login(username, password) {
      const session = await request<SessionResponse>(
        "/auth/login",
        "POST",
        { username, password },
        {},
        true,
      );
      if (
        typeof session.csrf_token !== "string" ||
        !session.csrf_token ||
        !isRecord(session.user) ||
        !["employee", "hr"].includes(String(session.user.role))
      ) {
        throw new ApiError(
          200,
          "INVALID_RESPONSE",
          "Сервер не выдал токен сессии. Повторите вход.",
        );
      }
      rememberCsrf(session.csrf_token);
      return session;
    },
    me: () => request("/me"),
    async logout() {
      const logoutGeneration = sessionGeneration;
      await request("/auth/logout", "POST");
      if (logoutGeneration === sessionGeneration) clearSession();
    },
    health: () => request("/health"),
    catalog: () => request("/catalog"),
    employee: (id) => request(employeePath(id)),
    employees: (limit = 20, offset = 0) =>
      request(`/employees?limit=${limit}&offset=${offset}`),
    recommendations: (id, scenarioDate) =>
      request(`${employeePath(id)}/recommendations`, "POST", {
        scenario_date: scenarioDate,
        limit: 3,
      }),
    latest: (id) => request(`${employeePath(id)}/recommendations/latest`),
    preview: (id, body) => request(`${employeePath(id)}/preview`, "POST", body),
    complete: (id, body, key) => {
      if (!key || key.length > 128 || !/^[\x21-\x7e]+$/.test(key)) {
        return Promise.reject(
          new ApiError(
            422,
            "IDEMPOTENCY_KEY_REQUIRED",
            "Не удалось определить операцию. Обновите страницу.",
          ),
        );
      }
      // Do not create a new key, replace a revision, or retry a mutation automatically.
      return request(`${employeePath(id)}/completions`, "POST", body, {
        "Idempotency-Key": key,
      });
    },
    goal: (id, body) => request(`${employeePath(id)}/goal`, "PATCH", body),
    hrSummary: () => request("/hr/summary"),
    importFiles: (body) => request("/hr/import", "POST", body),
  };
}
