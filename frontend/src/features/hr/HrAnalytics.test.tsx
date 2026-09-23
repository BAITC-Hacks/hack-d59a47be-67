import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CareerClient, HRAnalyticsResponse } from "../../api";
import { AppContext, type AppContextValue } from "../../context";
import { HrAnalytics } from "./HrAnalytics";
import { HrPage } from "./HrPage";

afterEach(cleanup);

function snapshot(): HRAnalyticsResponse {
  return {
    data_version: "synthetic-hr-test",
    state_version: 5,
    scenario_date: "2026-10-01",
    period_start: "2026-07-04",
    period_end: "2026-10-01",
    window_days: 90,
    employee_count: 3,
    employees_with_goal: 2,
    employees_with_history: 2,
    employees_with_completion_in_period: 1,
    history_records_in_period: 4,
    historical_proxy_records_in_period: 3,
    excluded_simulations: 1,
    skill_gaps: [
      {
        skill_id: "TEST_SYSTEMS",
        skill_name: "Проектирование сервисов",
        employees_requiring: 2,
        employees_with_gap: 1,
        gap_percent: 50,
        average_gap: 1.5,
        critical_gap_count: 1,
      },
    ],
    attention: [
      {
        profile: {
          employee_id: "TEST_NO_HISTORY",
          full_name: "Тестовый профиль без истории",
          department: "Команда примера",
          role: "Engineer",
          grade: "Middle",
        },
        reasons: [
          {
            code: "no_history",
            message:
              "В данных нет записей об участии. Уточните полноту истории.",
          },
        ],
        last_completed_date: null,
        history_records_in_period: 0,
        completed_in_period: 0,
        no_show_in_period: 0,
        eligible_event_count: 1,
        progress_percent: 75,
      },
      {
        profile: {
          employee_id: "TEST_NO_SHOW",
          full_name: "Тестовый профиль с пропусками",
          department: "Команда примера",
          role: "Engineer",
          grade: "Middle",
        },
        reasons: [
          {
            code: "repeated_no_show",
            message: "За выбранный период 2 записи со статусом no_show.",
          },
        ],
        last_completed_date: "2026-06-03",
        history_records_in_period: 2,
        completed_in_period: 0,
        no_show_in_period: 2,
        eligible_event_count: 1,
        progress_percent: 50,
      },
    ],
    participation: [
      {
        event_id: "TEST_EVENT",
        title: "Вымышленный практикум",
        type: "workshop",
        format: "online",
        mandatory: false,
        record_count: 4,
        participant_count: 2,
        completed: 1,
        in_progress: 1,
        dropped: 0,
        no_show: 2,
        declined: 0,
        overdue: 0,
      },
    ],
    notes: ["Синтетический fixture для проверки клиентского поведения."],
  };
}

function context(client: CareerClient): AppContextValue {
  return {
    client,
    catalog: null,
    mode: "live",
    user: { id: "TEST_HR", username: "test-hr", role: "hr", employee_id: null },
    handleError: vi.fn((cause: unknown) =>
      cause instanceof Error ? cause.message : "Ошибка",
    ),
  };
}

function setup(data = snapshot()) {
  const hrAnalytics = vi
    .fn<CareerClient["hrAnalytics"]>()
    .mockResolvedValue(data);
  const value = context({ hrAnalytics } as unknown as CareerClient);
  const onOpenEmployee = vi.fn();
  const content = (ctx = value, refresh = 0) => (
    <AppContext.Provider value={ctx}>
      <HrAnalytics onOpenEmployee={onOpenEmployee} refresh={refresh} />
    </AppContext.Provider>
  );
  const view = render(content());
  return {
    ...view,
    hrAnalytics,
    value,
    content,
    onOpenEmployee,
    user: userEvent.setup(),
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

describe("HR analytics", () => {
  it("uses the server cohort denominator and keeps absent history separate from no-show signals", async () => {
    const { hrAnalytics, onOpenEmployee, user } = setup();
    expect(hrAnalytics).toHaveBeenCalledWith(90);
    const gaps = await screen.findByRole("region", {
      name: "Какие навыки стоит развивать",
    });
    expect(within(gaps).getByText("1 из 2 сотрудников")).toBeInTheDocument();
    expect(within(gaps).getByRole("progressbar")).toHaveValue(50);
    expect(
      within(gaps).getByText("Средний разрыв: 1,5 ур."),
    ).toBeInTheDocument();
    await user.selectOptions(
      screen.getByLabelText("Показать основание"),
      "no_history",
    );
    expect(
      screen.getByText("Тестовый профиль без истории"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Тестовый профиль с пропусками"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Отсутствие истории означает отсутствие данных/),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "Обсудить развитие: Тестовый профиль без истории",
      }),
    );
    expect(onOpenEmployee).toHaveBeenCalledWith("TEST_NO_HISTORY");
    await user.selectOptions(
      screen.getByLabelText("Показать основание"),
      "repeated_no_show",
    );
    expect(
      screen.getByText("Тестовый профиль с пропусками"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Тестовый профиль без истории"),
    ).not.toBeInTheDocument();
  });

  it("requests a new server snapshot for the selected window and ignores a late older response", async () => {
    const { hrAnalytics, user } = setup();
    await screen.findByText("Проектирование сервисов");
    const older = deferred<HRAnalyticsResponse>();
    hrAnalytics.mockReturnValueOnce(older.promise);
    await user.selectOptions(screen.getByLabelText("Период участия"), "30");
    expect(hrAnalytics).toHaveBeenLastCalledWith(30);
    expect(
      screen.queryByText("Проектирование сервисов"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Считаем срез");
    const newest = snapshot();
    newest.window_days = 180;
    newest.skill_gaps[0].skill_name = "Актуальный срез за 180 дней";
    hrAnalytics.mockResolvedValueOnce(newest);
    await user.selectOptions(screen.getByLabelText("Период участия"), "180");
    expect(hrAnalytics).toHaveBeenLastCalledWith(180);
    await screen.findByText("Актуальный срез за 180 дней");
    await act(async () => older.resolve(snapshot()));
    expect(
      screen.queryByText("Проектирование сервисов"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Актуальный срез за 180 дней")).toBeInTheDocument();
  });

  it("distinguishes missing, stale, failed and disabled recommendations from an empty eligible catalogue", async () => {
    const cases = [
      {
        code: "recommendation_missing",
        label: "Подбор ещё не выполнен",
        name: "Подбор не запускали",
        message: "Допустимые активности есть, но подбор ещё не выполнен.",
        eligible: 2,
      },
      {
        code: "recommendation_stale",
        label: "Рекомендация устарела",
        name: "Изменился профиль",
        message:
          "Данные изменились после последнего подбора. Обновите рекомендации.",
        eligible: 2,
      },
      {
        code: "recommendation_unavailable",
        label: "Не удалось получить рекомендацию",
        name: "Ошибка подбора",
        message: "Последний подбор не дал результата. Повторите запрос.",
        eligible: 2,
      },
      {
        code: "ai_not_configured",
        label: "AI-подбор не настроен",
        name: "Подбор выключен",
        message: "AI-подбор не настроен. Обратитесь к администратору.",
        eligible: 2,
      },
      {
        code: "no_candidates",
        label: "Нет подходящих активностей",
        name: "Каталог не закрывает разрыв",
        message: "Среди доступных активностей нет подходящих для текущей цели.",
        eligible: 0,
      },
    ] as const;
    const data = snapshot();
    data.employee_count = cases.length;
    data.attention = cases.map((item) => ({
      ...data.attention[0],
      profile: {
        ...data.attention[0].profile,
        employee_id: `TEST_${item.code}`,
        full_name: item.name,
      },
      reasons: [{ code: item.code, message: item.message }],
      eligible_event_count: item.eligible,
      completed_in_period: 1,
      history_records_in_period: 1,
      last_completed_date: "2026-09-30",
    }));
    const { user } = setup(data);
    const support = await screen.findByRole("region", {
      name: "С кем обсудить следующий шаг",
    });
    for (const item of cases) {
      const card = within(support)
        .getByRole("heading", { name: item.name })
        .closest("li")!;
      expect(within(card).getByText(item.label)).toBeInTheDocument();
      expect(within(card).getByText(item.message)).toBeInTheDocument();
      if (item.eligible > 0) {
        expect(
          within(card).queryByText("Нет подходящих активностей"),
        ).not.toBeInTheDocument();
      }
    }
    for (const item of cases) {
      await user.selectOptions(
        screen.getByLabelText("Показать основание"),
        item.code,
      );
      expect(
        within(support).getByRole("heading", { level: 4, name: item.name }),
      ).toBeInTheDocument();
      expect(
        within(support).getAllByRole("heading", { level: 4 }),
      ).toHaveLength(1);
      expect(
        within(support).getByText(/Показано 1 из 1 сотрудников/),
      ).toBeInTheDocument();
    }
  });

  it("refreshes missing and stale recommendation signals from the server after recommendation and profile changes", async () => {
    const initial = snapshot();
    initial.attention = [
      {
        ...initial.attention[0],
        reasons: [
          {
            code: "recommendation_missing",
            message: "Подбор ещё не выполнен.",
          },
        ],
        eligible_event_count: 2,
      },
    ];
    const { hrAnalytics, content, rerender, value, user } = setup(initial);
    const support = await screen.findByRole("region", {
      name: "С кем обсудить следующий шаг",
    });
    await user.selectOptions(
      screen.getByLabelText("Показать основание"),
      "recommendation_missing",
    );
    expect(
      within(support).getByRole("heading", { level: 4 }),
    ).toBeInTheDocument();

    const recommended = { ...initial, attention: [] };
    hrAnalytics.mockResolvedValueOnce(recommended);
    rerender(content(value, 1));
    expect(screen.getByRole("status")).toHaveTextContent("Считаем срез");
    await screen.findByText(
      "С этим основанием сотрудников нет. Выберите другое основание.",
    );
    expect(screen.getByLabelText("Показать основание")).toHaveValue(
      "recommendation_missing",
    );
    expect(screen.queryByRole("heading", { level: 4 })).not.toBeInTheDocument();

    const changed = {
      ...initial,
      state_version: initial.state_version + 1,
      attention: [
        {
          ...initial.attention[0],
          reasons: [
            {
              code: "recommendation_stale" as const,
              message: "Данные изменились — обновите подбор.",
            },
          ],
        },
      ],
    };
    hrAnalytics.mockResolvedValueOnce(changed);
    rerender(content(value, 2));
    await screen.findByText(
      "С этим основанием сотрудников нет. Выберите другое основание.",
    );
    await user.selectOptions(
      screen.getByLabelText("Показать основание"),
      "recommendation_stale",
    );
    expect(
      screen.getByText("Данные изменились — обновите подбор."),
    ).toBeInTheDocument();
    expect(hrAnalytics).toHaveBeenCalledTimes(3);
  });

  it("ignores a previous session failure and does not invoke its error handler", async () => {
    const { hrAnalytics, user, content, rerender, value } = setup();
    await screen.findByText("Проектирование сервисов");
    const previous = deferred<HRAnalyticsResponse>();
    hrAnalytics.mockReturnValueOnce(previous.promise);
    await user.selectOptions(screen.getByLabelText("Период участия"), "30");
    const next = snapshot();
    next.skill_gaps[0].skill_name = "Данные новой сессии";
    const nextClient = {
      hrAnalytics: vi.fn().mockResolvedValue(next),
    } as unknown as CareerClient;
    rerender(content(context(nextClient)));
    await screen.findByText("Данные новой сессии");
    await act(async () =>
      previous.reject(new Error("Старая сессия завершена")),
    );
    expect(value.handleError).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lets HR retry analytics without blocking the employee directory and original summary", async () => {
    const user = userEvent.setup();
    const data = snapshot();
    const hrAnalytics = vi
      .fn<CareerClient["hrAnalytics"]>()
      .mockRejectedValueOnce(new Error("Сервер временно недоступен"))
      .mockResolvedValueOnce(data);
    const client = {
      hrAnalytics,
      hrSummary: vi.fn().mockResolvedValue({
        data_version: "test",
        employee_count: 3,
        employees_with_goal: 2,
        completion_count: 4,
        demo_simulation_count: 1,
      }),
      employees: vi.fn().mockResolvedValue({
        items: [data.attention[0].profile],
        total: 1,
        offset: 0,
        limit: 20,
      }),
    } as unknown as CareerClient;
    render(
      <AppContext.Provider value={context(client)}>
        <HrPage onOpenEmployee={vi.fn()} onOpenImport={vi.fn()} />
      </AppContext.Provider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Сервер временно недоступен",
    );
    expect(
      await screen.findByRole("button", {
        name: "Открыть профиль: Тестовый профиль без истории",
      }),
    ).toBeEnabled();
    expect(
      screen.getByRole("region", { name: "Сводка по команде" }),
    ).toHaveTextContent("Завершений в истории");
    await user.click(
      screen.getByRole("button", { name: "Повторить загрузку аналитики" }),
    );
    await screen.findByText("Проектирование сервисов");
    expect(hrAnalytics).toHaveBeenCalledTimes(2);
    expect(client.employees).toHaveBeenCalledTimes(1);
  });

  it("shows an explicit empty state instead of interpreting missing profiles as successful development", async () => {
    const data = snapshot();
    Object.assign(data, {
      employee_count: 0,
      employees_with_goal: 0,
      employees_with_history: 0,
      employees_with_completion_in_period: 0,
      history_records_in_period: 0,
      historical_proxy_records_in_period: 0,
      excluded_simulations: 0,
      skill_gaps: [],
      attention: [],
      participation: [],
    });
    const client = {
      hrAnalytics: vi.fn().mockResolvedValue(data),
    } as unknown as CareerClient;
    render(
      <AppContext.Provider value={context(client)}>
        <HrAnalytics onOpenEmployee={vi.fn()} refresh={0} />
      </AppContext.Provider>,
    );
    await screen.findByRole("heading", { name: "Для анализа нужны профили" });
    expect(
      screen.queryByRole("heading", { name: "Какие навыки стоит развивать" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/разрывов между навыками.*нет/),
    ).not.toBeInTheDocument();
  });

  it("exposes event record denominators and the full status breakdown with proxy and simulation caveats", async () => {
    const { user } = setup();
    const participation = await screen.findByRole("region", {
      name: "Участие в активностях",
    });
    expect(within(participation).getByText("1 / 4")).toBeInTheDocument();
    expect(
      within(participation).getByText(
        /Добровольная активность · 2 сотрудников/,
      ),
    ).toBeInTheDocument();
    const title = within(participation).getByText("Вымышленный практикум");
    await user.click(title);
    expect(title.closest("details")).toHaveAttribute("open");
    const status = within(participation).getByText("Пропущены").parentElement;
    expect(status).toHaveTextContent("2");
    expect(
      screen.getByText(/Учебных симуляций исключено: 1/),
    ).toHaveTextContent("за период: 3 из 4");
  });
});
