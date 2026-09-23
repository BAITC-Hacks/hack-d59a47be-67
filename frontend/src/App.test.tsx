import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type CareerClient } from "./api/client";
import { demoCatalog } from "./api/fixtures";
import type { CatalogResponse, UserIdentity } from "./api/types";
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
    health: vi.fn(),
    employee: vi.fn(),
    employees: vi.fn(),
    recommendations: vi.fn(),
    latest: vi.fn(),
    preview: vi.fn(),
    complete: vi.fn(),
    goal: vi.fn(),
    hrSummary: vi.fn(),
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
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("authentication and role boundaries", () => {
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
