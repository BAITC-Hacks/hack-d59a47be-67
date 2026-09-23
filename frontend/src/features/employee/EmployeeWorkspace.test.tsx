import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { ApiError, type CareerClient } from "../../api/client";
import {
  createDemoClient,
  DEMO_EMPLOYEE_ID,
  DEMO_SCENARIO_DATE,
  resetDemo,
} from "../../api/demo";
import { demoCatalog } from "../../api/fixtures";
import type {
  CompletionResponse,
  EmployeeDetailResponse,
  RecommendationResponse,
} from "../../api/types";
import { AppContext, type AppContextValue } from "../../context";
import EmployeeWorkspace from "./EmployeeWorkspace";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const identity = {
  id: "TEST_USER",
  username: "test",
  role: "employee" as const,
  employee_id: DEMO_EMPLOYEE_ID,
};
const handleError = vi.fn((error: unknown) =>
  error instanceof Error ? error.message : "Ошибка",
);
function context(client: CareerClient): AppContextValue {
  return {
    client,
    mode: "live",
    catalog: demoCatalog,
    user: identity,
    handleError,
  };
}
function workspace(client: CareerClient) {
  const value = context(client);
  return render(
    <AppContext.Provider value={value}>
      <EmployeeWorkspace
        employeeId={DEMO_EMPLOYEE_ID}
        view="overview"
        navigate={vi.fn()}
      />
    </AppContext.Provider>,
  );
}

const originalShow = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  "showModal",
);
const originalClose = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  "close",
);
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute("open");
    },
  });
});
afterAll(() => {
  if (originalShow)
    Object.defineProperty(
      HTMLDialogElement.prototype,
      "showModal",
      originalShow,
    );
  else Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
  if (originalClose)
    Object.defineProperty(HTMLDialogElement.prototype, "close", originalClose);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
});
beforeEach(() => {
  resetDemo();
  sessionStorage.clear();
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openFirstStep() {
  const user = userEvent.setup();
  await user.click(
    (await screen.findAllByRole("button", { name: "Подобрать шаги" }))[0],
  );
  const details = await screen.findAllByRole("button", {
    name: "Посмотреть результат",
  });
  await user.click(details[0]);
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByText(
    "Это предварительный расчёт. Ваши навыки пока не изменились.",
  );
  return { user, dialog };
}

describe("employee workflow", () => {
  it("retries an uncertain completion with the same key and body, then shows server progress and stale cards", async () => {
    const client = createDemoClient();
    const originalComplete = client.complete.bind(client);
    let lost = true;
    const complete = vi
      .spyOn(client, "complete")
      .mockImplementation(async (id, body, key) => {
        const response = await originalComplete(id, body, key);
        if (lost) {
          lost = false;
          throw new ApiError(0, "NETWORK_ERROR", "Ответ потерян");
        }
        return response;
      });
    workspace(client);
    const { user, dialog } = await openFirstStep();
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Подтверждаю, что действительно завершил(а) активность",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Завершить активность" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Ответ потерян",
    );
    expect(
      within(dialog).getByRole("checkbox", { name: /Симуляция результата/ }),
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", { name: "Повторить подтверждение" }),
    );
    await screen.findByText("Ещё один шаг к вашей цели!");
    expect(complete).toHaveBeenCalledTimes(2);
    expect(complete.mock.calls[1]).toEqual(complete.mock.calls[0]);
    expect(complete.mock.calls[1][1].expected_state_version).toBe(0);
    expect(
      screen.getByText(
        "Ваш профиль изменился. Обновите подборку, чтобы увидеть актуальные шаги.",
      ),
    ).toBeVisible();
    screen
      .getAllByRole("button", { name: "Посмотреть результат" })
      .forEach((button) => expect(button).toBeDisabled());
    expect(screen.getByText(/Соответствие цели:/)).toHaveTextContent(
      "72,7% → 86,4%",
    );
    expect((await client.employee(DEMO_EMPLOYEE_ID)).history).toHaveLength(2);
  });

  it("keeps the dialog editable after a definite rejection instead of replaying a rejected command", async () => {
    const client = createDemoClient();
    const complete = vi
      .spyOn(client, "complete")
      .mockRejectedValue(
        new ApiError(409, "REVISION_CONFLICT", "Данные изменились"),
      );
    workspace(client);
    const { user, dialog } = await openFirstStep();
    const confirmation = within(dialog).getByRole("checkbox", {
      name: "Подтверждаю, что действительно завершил(а) активность",
    });
    await user.click(confirmation);
    await user.click(
      within(dialog).getByRole("button", { name: "Завершить активность" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Данные изменились",
    );
    expect(confirmation).toBeEnabled();
    expect(
      within(dialog).queryByRole("button", { name: "Повторить подтверждение" }),
    ).not.toBeInTheDocument();
    expect(complete).toHaveBeenCalledOnce();
  });

  it("ignores preview errors from a dialog that the user already closed", async () => {
    const client = createDemoClient();
    const preview = deferred<never>();
    vi.spyOn(client, "preview").mockReturnValue(preview.promise);
    workspace(client);
    const user = userEvent.setup();
    await user.click(
      (await screen.findAllByRole("button", { name: "Подобрать шаги" }))[0],
    );
    await user.click(
      (
        await screen.findAllByRole("button", { name: "Посмотреть результат" })
      )[0],
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getAllByRole("button", { name: "Закрыть" })[0],
    );
    await act(async () =>
      preview.reject(new ApiError(401, "UNAUTHENTICATED", "Старая сессия")),
    );
    expect(handleError).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not overwrite freshly generated recommendations with a delayed latest response", async () => {
    const client = createDemoClient();
    const oldResponse = await client.recommendations(
      DEMO_EMPLOYEE_ID,
      DEMO_SCENARIO_DATE,
    );
    const oldLatest = deferred<RecommendationResponse>();
    vi.spyOn(client, "latest").mockReturnValue(oldLatest.promise);
    const freshResponse = structuredClone(oldResponse);
    freshResponse.recommendations[0].title = "Новая подтверждённая подборка";
    vi.spyOn(client, "recommendations").mockResolvedValue(freshResponse);
    workspace(client);
    const user = userEvent.setup();
    await user.click(
      (await screen.findAllByRole("button", { name: "Подобрать шаги" }))[0],
    );
    await screen.findByRole("heading", {
      name: "Новая подтверждённая подборка",
    });
    await act(async () => oldLatest.resolve(oldResponse));
    expect(
      screen.getByRole("heading", { name: "Новая подтверждённая подборка" }),
    ).toBeInTheDocument();
  });

  it("does not let an old generation failure affect the next employee connection", async () => {
    const previous = createDemoClient();
    const pendingRecommendations = deferred<RecommendationResponse>();
    vi.spyOn(previous, "recommendations").mockReturnValue(
      pendingRecommendations.promise,
    );
    const view = workspace(previous);
    const user = userEvent.setup();
    await user.click(
      (await screen.findAllByRole("button", { name: "Подобрать шаги" }))[0],
    );
    const next = createDemoClient();
    view.rerender(
      <AppContext.Provider value={context(next)}>
        <EmployeeWorkspace
          employeeId={DEMO_EMPLOYEE_ID}
          view="overview"
          navigate={vi.fn()}
        />
      </AppContext.Provider>,
    );
    await screen.findByRole("heading", { name: "Моё развитие" });
    await act(async () =>
      pendingRecommendations.reject(
        new ApiError(401, "UNAUTHENTICATED", "Предыдущая сессия истекла"),
      ),
    );
    expect(handleError).not.toHaveBeenCalled();
    expect(
      screen.queryByText("Предыдущая сессия истекла"),
    ).not.toBeInTheDocument();
  });

  it("ignores a delayed profile result after the employee identity changed", async () => {
    const previous = createDemoClient();
    const previousProfile = await previous.employee(DEMO_EMPLOYEE_ID);
    previousProfile.profile.full_name = "Старый пользователь";
    const pendingProfile = deferred<EmployeeDetailResponse>();
    vi.spyOn(previous, "employee").mockReturnValue(pendingProfile.promise);
    const view = workspace(previous);
    const next = createDemoClient();
    view.rerender(
      <AppContext.Provider value={context(next)}>
        <EmployeeWorkspace
          employeeId={DEMO_EMPLOYEE_ID}
          view="overview"
          navigate={vi.fn()}
        />
      </AppContext.Provider>,
    );
    await screen.findByText(/Алия Садыкова · Backend Engineer · Middle/);
    await act(async () => pendingProfile.resolve(previousProfile));
    expect(
      screen.getByText(/Алия Садыкова · Backend Engineer · Middle/),
    ).toBeVisible();
    expect(screen.queryByText(/Старый пользователь/)).not.toBeInTheDocument();
  });

  it("does not publish an old recommendation response as actionable after a completion changed the profile", async () => {
    const client = createDemoClient();
    const oldResponse = await client.recommendations(
      DEMO_EMPLOYEE_ID,
      DEMO_SCENARIO_DATE,
    );
    const pendingRecommendations = deferred<RecommendationResponse>();
    vi.spyOn(client, "latest").mockRejectedValue(
      new ApiError(404, "RECOMMENDATION_NOT_FOUND", "Нет подборки"),
    );
    vi.spyOn(client, "recommendations").mockReturnValue(
      pendingRecommendations.promise,
    );
    const value = context(client);
    const navigate = vi.fn();
    const renderView = (view: "overview" | "catalog") => (
      <AppContext.Provider value={value}>
        <EmployeeWorkspace
          employeeId={DEMO_EMPLOYEE_ID}
          view={view}
          navigate={navigate}
        />
      </AppContext.Provider>
    );
    const view = render(renderView("overview"));
    const user = userEvent.setup();
    await user.click(
      (await screen.findAllByRole("button", { name: "Подобрать шаги" }))[0],
    );
    view.rerender(renderView("catalog"));
    await user.click(
      (
        await screen.findAllByRole("button", {
          name: "Проверить для моей цели",
        })
      )[0],
    );
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(
      "Это предварительный расчёт. Ваши навыки пока не изменились.",
    );
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Подтверждаю, что действительно завершил(а) активность",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Завершить активность" }),
    );
    await screen.findByText("Ещё один шаг к вашей цели!");
    await act(async () => pendingRecommendations.resolve(oldResponse));
    view.rerender(renderView("overview"));
    screen
      .queryAllByRole("button", { name: "Посмотреть результат" })
      .forEach((button) => expect(button).toBeDisabled());
    expect(screen.getByText(/Соответствие цели:/)).toHaveTextContent("86,4%");
  });

  it("does not report a previous session completion error after the workspace unmounted", async () => {
    const client = createDemoClient();
    const completion = deferred<CompletionResponse>();
    vi.spyOn(client, "complete").mockReturnValue(completion.promise);
    const view = workspace(client);
    const { user, dialog } = await openFirstStep();
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Подтверждаю, что действительно завершил(а) активность",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Завершить активность" }),
    );
    view.unmount();
    await act(async () =>
      completion.reject(
        new ApiError(401, "UNAUTHENTICATED", "Предыдущая сессия истекла"),
      ),
    );
    expect(handleError).not.toHaveBeenCalled();
  });

  it("restores an uncertain simulation after closing and remounting, without checking a now-stale revision first", async () => {
    const client = createDemoClient();
    const originalComplete = client.complete.bind(client);
    let lost = true;
    const complete = vi
      .spyOn(client, "complete")
      .mockImplementation(async (id, body, key) => {
        const response = await originalComplete(id, body, key);
        if (lost) {
          lost = false;
          throw new ApiError(0, "NETWORK_ERROR", "Ответ потерян");
        }
        return response;
      });
    const preview = vi.spyOn(client, "preview");
    const view = workspace(client);
    const { user, dialog } = await openFirstStep();
    await user.click(
      within(dialog).getByRole("checkbox", { name: /Симуляция результата/ }),
    );
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Понимаю: симуляция будет сохранена отдельно в истории",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Сохранить симуляцию" }),
    );
    await within(dialog).findByRole("button", {
      name: "Повторить подтверждение",
    });
    await user.click(
      within(dialog).getAllByRole("button", { name: "Закрыть" })[0],
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    view.unmount();
    workspace(client);
    const restoredDialog = await screen.findByRole("dialog");
    expect(
      within(restoredDialog).getByRole("checkbox", {
        name: /Симуляция результата/,
      }),
    ).toBeChecked();
    expect(
      within(restoredDialog).getByRole("checkbox", {
        name: /Симуляция результата/,
      }),
    ).toBeDisabled();
    await user.click(
      within(restoredDialog).getByRole("button", {
        name: "Повторить подтверждение",
      }),
    );
    await screen.findByText("Симуляция сохранена отдельно");
    expect(complete.mock.calls[1]).toEqual(complete.mock.calls[0]);
    expect(complete.mock.calls[1][1]).toMatchObject({
      expected_state_version: 0,
      mode: "demo_simulation",
    });
    expect(preview).toHaveBeenCalledOnce();
    expect(
      Object.keys(sessionStorage).filter((key) =>
        key.startsWith("career-quest.pending-completion"),
      ),
    ).toHaveLength(0);
    expect((await client.employee(DEMO_EMPLOYEE_ID)).history).toHaveLength(2);
  });

  it("does not restore another account's pending completion", async () => {
    const client = createDemoClient();
    vi.spyOn(client, "complete").mockRejectedValue(
      new ApiError(0, "NETWORK_ERROR", "Ответ потерян"),
    );
    const view = workspace(client);
    const { user, dialog } = await openFirstStep();
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Подтверждаю, что действительно завершил(а) активность",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Завершить активность" }),
    );
    await within(dialog).findByRole("button", {
      name: "Повторить подтверждение",
    });
    view.unmount();
    const nextContext = {
      ...context(client),
      user: { ...identity, id: "ANOTHER_ACCOUNT" },
    };
    render(
      <AppContext.Provider value={nextContext}>
        <EmployeeWorkspace
          employeeId={DEMO_EMPLOYEE_ID}
          view="overview"
          navigate={vi.fn()}
        />
      </AppContext.Provider>,
    );
    await screen.findByRole("heading", { name: "Моё развитие" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers a profile refresh after a definite revision conflict and removes the rejected command", async () => {
    const client = createDemoClient();
    const employee = vi.spyOn(client, "employee");
    workspace(client);
    const { user, dialog } = await openFirstStep();
    // An independent server-side action changes the revision after preview.
    await client.complete(
      DEMO_EMPLOYEE_ID,
      {
        expected_state_version: 0,
        event_id: "DEMO_PYTHON",
        mode: "completion",
      },
      "external-action",
    );
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Подтверждаю, что действительно завершил(а) активность",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Завершить активность" }),
    );
    await within(dialog).findByRole("alert");
    expect(
      within(dialog).queryByRole("button", { name: "Завершить активность" }),
    ).not.toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Обновить профиль" }),
    );
    await waitFor(() => expect(employee).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("82")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      Object.keys(sessionStorage).filter((key) =>
        key.startsWith("career-quest.pending-completion"),
      ),
    ).toHaveLength(0);
  });

  it("ignores a late goal error after the account workspace was removed", async () => {
    const client = createDemoClient();
    const goal = deferred<EmployeeDetailResponse>();
    vi.spyOn(client, "goal").mockReturnValue(goal.promise);
    const view = workspace(client);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Изменить цель" }),
    );
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Сохранить цель",
      }),
    );
    view.unmount();
    await act(async () =>
      goal.reject(
        new ApiError(401, "UNAUTHENTICATED", "Предыдущая сессия истекла"),
      ),
    );
    expect(handleError).not.toHaveBeenCalled();
  });

  it("can complete a specific overdue mandatory assignment and sends its record ID", async () => {
    const client = createDemoClient();
    const profile = await client.employee(DEMO_EMPLOYEE_ID);
    profile.history = [
      {
        record_id: "ASSIGNMENT_42",
        event_id: "DEMO_ONBOARDING",
        status: "overdue",
        activity_date: "2026-09-01",
        date_source: "historical_proxy",
        completed_at: null,
        mode: "import",
      },
    ];
    vi.spyOn(client, "employee").mockResolvedValue(profile);
    const preview = vi.spyOn(client, "preview");
    const complete = vi.spyOn(client, "complete").mockResolvedValue({
      state_version: 1,
      completion: {
        completion_id: "ASSIGNMENT_COMPLETION",
        event_id: "DEMO_ONBOARDING",
        mode: "completion",
        completed_at: "2026-10-01T12:00:00+05:00",
        effects: [],
      },
    });
    render(
      <AppContext.Provider value={context(client)}>
        <EmployeeWorkspace
          employeeId={DEMO_EMPLOYEE_ID}
          view="history"
          navigate={vi.fn()}
        />
      </AppContext.Provider>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Завершить" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Подтверждаю, что действительно завершил(а) активность",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Завершить активность" }),
    );
    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    expect(complete.mock.calls[0][1]).toMatchObject({
      record_id: "ASSIGNMENT_42",
      event_id: "DEMO_ONBOARDING",
      mode: "completion",
    });
    expect(preview).not.toHaveBeenCalled();
  });
});
