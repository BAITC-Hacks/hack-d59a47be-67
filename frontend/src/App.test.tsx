import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type CareerClient } from "./api/client";
import { demoCatalog } from "./api/fixtures";
import type {
  CatalogResponse,
  SessionResponse,
  UserIdentity,
} from "./api/types";
import App from "./App";

const api = vi.hoisted(() => ({
  createApiClient: vi.fn(),
  getCsrf: vi.fn(),
  clearSession: vi.fn(),
  createDemoClient: vi.fn(),
  resetDemo: vi.fn(),
}));
vi.mock("./api/client", async (importActual) => ({
  ...(await importActual<typeof import("./api/client")>()),
  createApiClient: api.createApiClient,
  getCsrf: api.getCsrf,
  clearSession: api.clearSession,
}));
vi.mock("./api/demo", () => ({
  createDemoClient: api.createDemoClient,
  resetDemo: api.resetDemo,
}));
vi.mock("./features/employee/EmployeeWorkspace", () => ({
  default: ({ employeeId, view }: { employeeId: string; view: string }) => (
    <div data-testid="employee-screen">
      {employeeId} · {view}
    </div>
  ),
}));
vi.mock("./features/hr/HrPage", () => ({
  HrPage: () => <div data-testid="hr-screen">HR screen</div>,
}));
vi.mock("./features/import/ImportPage", () => ({
  ImportPage: () => <div data-testid="import-screen">Import screen</div>,
}));

const employee: UserIdentity = {
  id: "TEST_USER",
  username: "employee-user",
  role: "employee",
  employee_id: "OWN_EMPLOYEE",
};
const hr: UserIdentity = {
  id: "TEST_HR",
  username: "hr-user",
  role: "hr",
  employee_id: null,
};
function client(identity = employee): CareerClient {
  return {
    me: vi.fn().mockResolvedValue(identity),
    catalog: vi.fn().mockResolvedValue(demoCatalog),
    logout: vi.fn().mockResolvedValue(undefined),
    login: vi.fn().mockResolvedValue({
      user: identity,
      csrf_token: "csrf",
      expires_at: "2099-01-01T00:00:00Z",
    }),
    publicConfig: vi.fn().mockResolvedValue({
      enabled: false,
      session_ttl_seconds: 7200,
      ai_enabled: false,
    }),
    publicSession: vi
      .fn()
      .mockImplementation(async (role: "employee" | "hr") => ({
        user: role === "hr" ? hr : employee,
        csrf_token: "public-csrf",
        expires_at: "2099-01-01T00:00:00Z",
      })),
    health: vi.fn(),
    employee: vi.fn(),
    employees: vi.fn(),
    recommendations: vi.fn(),
    latest: vi.fn(),
    preview: vi.fn(),
    complete: vi.fn(),
    goal: vi.fn(),
    hrSummary: vi.fn(),
    hrAnalytics: vi.fn(),
    importFiles: vi.fn(),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
let live: CareerClient;

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  window.history.replaceState({}, "", "/#/overview");
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  live = client();
  api.createApiClient.mockReturnValue(live);
  api.getCsrf.mockReturnValue(null);
  api.createDemoClient.mockImplementation((role: string) =>
    client(role === "hr" ? hr : employee),
  );
});

describe("public server entry", () => {
  function enablePublic(aiEnabled = false) {
    vi.mocked(live.publicConfig).mockResolvedValue({
      enabled: true,
      session_ttl_seconds: 7200,
      ai_enabled: aiEnabled,
    });
  }

  it("opens the actual API session only once and clearly explains synthetic data and disabled AI", async () => {
    enablePublic();
    const pending = deferred<SessionResponse>();
    vi.mocked(live.publicSession).mockReturnValueOnce(pending.promise);
    const user = userEvent.setup();
    render(<App />);
    const entry = await screen.findByRole("button", {
      name: "Кабинет сотрудника",
    });
    expect(
      screen.queryByRole("button", { name: "Войти в кабинет" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Посмотреть демо" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/отдельную копию данных для вашего браузера/),
    ).toBeVisible();
    expect(screen.getByText(/AI сейчас отключён/)).toBeVisible();
    await user.dblClick(entry);
    expect(live.publicSession).toHaveBeenCalledExactlyOnceWith("employee");
    expect(screen.getByRole("button", { name: "Кабинет HR" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Открываем вашу учебную копию",
    );
    await act(async () =>
      pending.resolve({
        user: employee,
        csrf_token: "public-csrf",
        expires_at: "2099-01-01T00:00:00Z",
      }),
    );
    expect(await screen.findByTestId("employee-screen")).toBeVisible();
    expect(
      screen.getByRole("complementary", { name: "Публичная учебная сессия" }),
    ).toHaveTextContent("настоящий сервер");
    expect(sessionStorage.getItem("cq-ui-mode")).toBe("live");
    expect(api.createDemoClient).not.toHaveBeenCalled();
    expect(live.login).not.toHaveBeenCalled();
    expect(live.catalog).toHaveBeenCalled();
  });

  it("switches public roles through the server and immediately removes controls from the previous role", async () => {
    enablePublic(true);
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Кабинет сотрудника" }),
    );
    await screen.findByTestId("employee-screen");
    expect(
      screen.queryByRole("link", { name: "Пример профилей JSON" }),
    ).not.toBeInTheDocument();
    const pending = deferred<SessionResponse>();
    vi.mocked(live.publicSession).mockReturnValueOnce(pending.promise);
    await user.click(
      screen.getByRole("button", { name: "Перейти в кабинет HR" }),
    );
    expect(screen.queryByTestId("employee-screen")).not.toBeInTheDocument();
    expect(live.publicSession).toHaveBeenLastCalledWith("hr");
    await act(async () =>
      pending.resolve({
        user: hr,
        csrf_token: "hr-public-csrf",
        expires_at: "2099-01-01T00:00:00Z",
      }),
    );
    expect(await screen.findByTestId("hr-screen")).toBeVisible();
    expect(screen.getByRole("link", { name: "Импорт данных" })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Пример профилей JSON" }),
    ).toHaveAttribute("href", "/api/public/examples/employees.json");
    expect(
      screen.getByRole("link", { name: "Пример истории CSV" }),
    ).toHaveAttribute("download", "activity_history.csv");
    expect(screen.queryByText(/AI отключён/)).not.toBeInTheDocument();
    expect(api.createDemoClient).not.toHaveBeenCalled();
  });

  it("keeps public entry retryable after a capacity error without falling back to prepared responses", async () => {
    enablePublic();
    vi.mocked(live.publicSession).mockRejectedValueOnce(
      new ApiError(
        429,
        "DEMO_CAPACITY",
        "Все учебные места заняты. Попробуйте позже.",
      ),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Кабинет HR" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Все учебные места заняты",
    );
    expect(screen.getByRole("button", { name: "Кабинет HR" })).toBeEnabled();
    expect(screen.queryByTestId("hr-screen")).not.toBeInTheDocument();
    expect(api.createDemoClient).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Кабинет HR" }));
    expect(await screen.findByTestId("hr-screen")).toBeVisible();
    expect(live.publicSession).toHaveBeenCalledTimes(2);
  });

  it("can retry discovery after a network failure while preserving ordinary private entry", async () => {
    vi.mocked(live.publicConfig)
      .mockRejectedValueOnce(new Error("Нет сети"))
      .mockResolvedValueOnce({
        enabled: true,
        session_ttl_seconds: 7200,
        ai_enabled: false,
      });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Проверить быстрый доступ" });
    expect(
      screen.getByRole("button", { name: "Войти в кабинет" }),
    ).toBeVisible();
    expect(live.publicSession).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Проверить быстрый доступ" }),
    );
    expect(
      await screen.findByRole("button", { name: "Кабинет сотрудника" }),
    ).toBeVisible();
    expect(live.publicConfig).toHaveBeenCalledTimes(2);
  });

  it("does not navigate or persist a late entry result after the app has unmounted", async () => {
    enablePublic();
    const pending = deferred<SessionResponse>();
    vi.mocked(live.publicSession).mockReturnValueOnce(pending.promise);
    const user = userEvent.setup();
    const view = render(<App />);
    await user.click(await screen.findByRole("button", { name: "Кабинет HR" }));
    view.unmount();
    await act(async () =>
      pending.resolve({
        user: hr,
        csrf_token: "old-csrf",
        expires_at: "2099-01-01T00:00:00Z",
      }),
    );
    expect(window.location.hash).toBe("#/overview");
    expect(sessionStorage.getItem("cq-ui-mode")).toBeNull();
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("authentication and role boundaries", () => {
  it("preserves password login for a private server and uses its returned identity", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText("Логин"), "private-user");
    await user.type(screen.getByLabelText("Пароль"), "synthetic-test-password");
    await user.click(screen.getByRole("button", { name: "Войти в кабинет" }));
    expect(await screen.findByTestId("employee-screen")).toHaveTextContent(
      "OWN_EMPLOYEE",
    );
    expect(live.login).toHaveBeenCalledExactlyOnceWith(
      "private-user",
      "synthetic-test-password",
    );
    expect(live.publicSession).not.toHaveBeenCalled();
    expect(api.createDemoClient).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("complementary", { name: "Публичная учебная сессия" }),
    ).not.toBeInTheDocument();
  });

  it("requires login when CSRF was lost and does not try a silent demo or unusable cookie session", async () => {
    render(<App />);
    expect(
      await screen.findByRole("button", { name: "Войти в кабинет" }),
    ).toBeVisible();
    expect(live.me).not.toHaveBeenCalled();
    expect(api.createDemoClient).not.toHaveBeenCalled();
    expect(screen.queryByTestId("employee-screen")).not.toBeInTheDocument();
  });

  it("always uses the server-bound employee ID even with a forged employee or HR hash", async () => {
    api.getCsrf.mockReturnValue("valid-token");
    window.history.replaceState({}, "", "/#/employee/SOMEONE_ELSE");
    render(<App />);
    expect(await screen.findByTestId("employee-screen")).toHaveTextContent(
      "OWN_EMPLOYEE · overview",
    );
    expect(screen.queryByTestId("hr-screen")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Импорт данных" }),
    ).not.toBeInTheDocument();
    await act(async () => {
      window.location.hash = "/import";
    });
    expect(screen.getByTestId("employee-screen")).toHaveTextContent(
      "OWN_EMPLOYEE",
    );
    expect(screen.queryByTestId("import-screen")).not.toBeInTheDocument();
  });

  it("uses the HR interface only for a server HR identity", async () => {
    api.getCsrf.mockReturnValue("valid-token");
    live = client(hr);
    api.createApiClient.mockReturnValue(live);
    window.history.replaceState({}, "", "/#/import");
    render(<App />);
    expect(await screen.findByTestId("import-screen")).toBeVisible();
    expect(screen.getByRole("link", { name: "Обзор команды" })).toBeVisible();
    expect(screen.queryByTestId("employee-screen")).not.toBeInTheDocument();
  });

  it("returns to login after an expired restored session and clears the obsolete token", async () => {
    api.getCsrf.mockReturnValue("expired-token");
    vi.mocked(live.me).mockRejectedValue(
      new ApiError(
        401,
        "UNAUTHENTICATED",
        "Сессия завершилась. Войдите снова.",
      ),
    );
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Сессия завершилась",
    );
    expect(
      screen.getByRole("button", { name: "Войти в кабинет" }),
    ).toBeVisible();
    expect(api.clearSession).toHaveBeenCalled();
    expect(live.catalog).not.toHaveBeenCalled();
  });

  it("shows connection failure without replacing a real account with demo data", async () => {
    api.getCsrf.mockReturnValue("existing-token");
    vi.mocked(live.me).mockRejectedValue(
      new ApiError(0, "NETWORK_ERROR", "Нет соединения с сервером."),
    );
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Нет соединения",
    );
    expect(api.createDemoClient).not.toHaveBeenCalled();
    expect(screen.queryByTestId("employee-screen")).not.toBeInTheDocument();
  });

  it("allows explicit demo entry and role switching with an always visible disclosure", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Посмотреть демо" }),
    );
    expect(
      await screen.findByText(
        "Вымышленные данные и готовые примеры. Реальный AI не вызывается.",
      ),
    ).toBeVisible();
    expect(screen.getByTestId("employee-screen")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Посмотреть HR" }));
    expect(await screen.findByTestId("hr-screen")).toBeVisible();
    expect(screen.queryByTestId("employee-screen")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Вымышленные данные и готовые примеры. Реальный AI не вызывается.",
      ),
    ).toBeVisible();
  });

  it("keeps the active account when the server did not confirm logout", async () => {
    const user = userEvent.setup();
    api.getCsrf.mockReturnValue("existing-token");
    vi.mocked(live.logout).mockRejectedValue(
      new ApiError(0, "NETWORK_ERROR", "Не удалось подтвердить выход."),
    );
    render(<App />);
    await screen.findByTestId("employee-screen");
    await user.click(screen.getByRole("button", { name: "Выйти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось подтвердить выход",
    );
    expect(screen.getByTestId("employee-screen")).toBeInTheDocument();
    expect(api.clearSession).not.toHaveBeenCalled();
  });

  it("ignores an old account catalog failure after a new demo connection was selected", async () => {
    const user = userEvent.setup();
    const oldCatalog = deferred<CatalogResponse>();
    api.getCsrf.mockReturnValue("existing-token");
    vi.mocked(live.catalog).mockReturnValue(oldCatalog.promise);
    render(<App />);
    await screen.findByTestId("employee-screen");
    api.getCsrf.mockReturnValue(null);
    await user.click(screen.getByRole("button", { name: "Выйти" }));
    await user.click(
      await screen.findByRole("button", { name: "Посмотреть демо" }),
    );
    await screen.findByTestId("employee-screen");
    await act(async () =>
      oldCatalog.reject(
        new ApiError(401, "UNAUTHENTICATED", "Старая сессия истекла"),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Войти в кабинет" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("employee-screen")).toBeVisible();
  });
});
