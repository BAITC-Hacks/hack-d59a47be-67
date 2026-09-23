import { useEffect, useState } from "react";
import { ArrowRight, CalendarDays, RefreshCw, Users } from "lucide-react";
import type { HRAnalyticsResponse } from "../../api";
import { useApp } from "../../context";

type Props = {
  onOpenEmployee: (id: string) => void;
  refresh: number;
};

const count = (value: number) => value.toLocaleString("ru-RU");
const number = (value: number) =>
  value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
const date = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));

const reasons = {
  no_history: "Нет данных об участии",
  no_recent_completion: "Нет завершений за период",
  repeated_no_show: "Повторные пропуски",
  no_candidates: "Нет доступного следующего шага",
  no_target: "Нужно обсудить цель",
};

type ReasonCode = keyof typeof reasons;

/** All analytics and support signals come from the server, including their denominators. */
export function HrAnalytics({ onOpenEmployee, refresh }: Props) {
  const { client, handleError } = useApp();
  const [windowDays, setWindowDays] = useState(90);
  const [data, setData] = useState<HRAnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [reason, setReason] = useState<"all" | ReasonCode>("all");
  const [visiblePeople, setVisiblePeople] = useState(10);
  const [visibleEvents, setVisibleEvents] = useState(8);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setData(null);
    setError("");
    setVisiblePeople(10);
    setVisibleEvents(8);
    client
      .hrAnalytics(windowDays)
      .then((response) => {
        if (active) setData(response);
      })
      .catch((cause: unknown) => {
        if (active) setError(handleError(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [client, windowDays, refresh, retry, handleError]);

  const attention =
    data?.attention.filter(
      (employee) =>
        reason === "all" ||
        employee.reasons.some((item) => item.code === reason),
    ) ?? [];
  const gaps =
    data?.skill_gaps.filter((skill) => skill.employees_with_gap > 0) ?? [];

  return (
    <section className="hr-analytics" aria-labelledby="hr-analytics-title">
      <div className="hr-analytics-heading">
        <div>
          <p className="eyebrow">ОТ ДАННЫХ К ДЕЙСТВИЮ</p>
          <h2 id="hr-analytics-title">Где команде нужна поддержка</h2>
          <p className="muted">
            Пробелы до карьерной цели и факты участия помогают обсудить
            следующий шаг. Это не оценка эффективности сотрудника.
          </p>
        </div>
        <label className="hr-period-control">
          <span>Период участия</span>
          <select
            value={windowDays}
            onChange={(event) => setWindowDays(Number(event.target.value))}
          >
            <option value={30}>30 дней</option>
            <option value={90}>90 дней</option>
            <option value={180}>180 дней</option>
          </select>
        </label>
      </div>

      <div aria-busy={loading}>
        {loading && (
          <div className="card hr-analytics-status" role="status">
            <RefreshCw className="hr-spinning" size={20} aria-hidden="true" />
            Считаем срез по данным всей команды…
          </div>
        )}
        {!loading && error && (
          <div className="alert hr-error" role="alert">
            <div>
              <strong>Не удалось загрузить аналитику</strong>
              <p>{error}</p>
            </div>
            <button
              className="button button-secondary"
              onClick={() => setRetry((value) => value + 1)}
            >
              Повторить загрузку аналитики
            </button>
          </div>
        )}
        {!loading && data && data.employee_count === 0 && (
          <div className="card empty-state hr-empty">
            <Users size={30} aria-hidden="true" />
            <h3>Для анализа нужны профили</h3>
            <p className="muted">
              Загрузите сотрудников и историю через «Загрузить данные». После
              импорта появятся разрывы навыков и факты участия.
            </p>
          </div>
        )}
        {!loading && data && data.employee_count > 0 && (
          <div className="hr-analytics-body">
            <div className="hr-analytics-period">
              <CalendarDays size={17} aria-hidden="true" />
              <p>
                Участие:{" "}
                <strong>
                  {date(data.period_start)} — {date(data.period_end)}
                </strong>
                . Навыки и цели: срез на{" "}
                <strong>{date(data.scenario_date)}</strong>.
              </p>
            </div>

            <div
              className="hr-participation-overview"
              aria-label="Охват данных команды"
            >
              <div>
                <span>Завершали за период</span>
                <strong>
                  {count(data.employees_with_completion_in_period)}{" "}
                  <small>из {count(data.employee_count)}</small>
                </strong>
                <p>Сотрудников команды, хотя бы одно завершение</p>
              </div>
              <div>
                <span>Есть история участия</span>
                <strong>
                  {count(data.employees_with_history)}{" "}
                  <small>из {count(data.employee_count)}</small>
                </strong>
                <p>Сотрудников с записями на дату среза</p>
              </div>
              <div>
                <span>Есть карьерная цель</span>
                <strong>
                  {count(data.employees_with_goal)}{" "}
                  <small>из {count(data.employee_count)}</small>
                </strong>
                <p>Личная цель или следующая ступень</p>
              </div>
            </div>

            <section
              className="card hr-analytics-card"
              aria-labelledby="hr-gaps-title"
            >
              <h3 id="hr-gaps-title">Какие навыки стоит развивать</h3>
              <p className="muted hr-analytics-description">
                Доля считается среди сотрудников, у которых этот навык входит в
                требования цели. Критичные навыки влияют на следующий карьерный
                шаг.
              </p>
              {gaps.length === 0 ? (
                <p className="hr-inline-empty">
                  {data.employees_with_goal === 0
                    ? "Цели пока не определены — сравнить навыки с требованиями нельзя."
                    : "По текущим данным разрывов между навыками и требованиями целей нет."}
                </p>
              ) : (
                <ul className="hr-gap-list">
                  {gaps.map((skill) => (
                    <li key={skill.skill_id}>
                      <div className="hr-gap-heading">
                        <strong>{skill.skill_name}</strong>
                        <span>
                          <b>{number(skill.gap_percent)}%</b> с разрывом
                        </span>
                      </div>
                      <progress
                        max={100}
                        value={skill.gap_percent}
                        aria-label={`Доля сотрудников с разрывом: ${skill.skill_name}`}
                      />
                      <div className="hr-gap-facts">
                        <span>
                          {count(skill.employees_with_gap)} из{" "}
                          {count(skill.employees_requiring)} сотрудников
                        </span>
                        <span>
                          Средний разрыв: {number(skill.average_gap)} ур.
                        </span>
                        <span>
                          Критичен для {count(skill.critical_gap_count)}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <p className="hr-fine-print">
                Средний разрыв рассчитан только среди сотрудников с
                недостаточным уровнем навыка.
              </p>
            </section>

            <section
              className="card hr-analytics-card"
              aria-labelledby="hr-support-title"
            >
              <div className="hr-support-heading">
                <div>
                  <h3 id="hr-support-title">С кем обсудить следующий шаг</h3>
                  <p className="muted hr-analytics-description">
                    Основания для разговора, а не рейтинг сотрудников.
                    Отсутствие истории означает отсутствие данных, а не низкую
                    вовлечённость.
                  </p>
                </div>
                <label className="hr-reason-control">
                  <span>Показать основание</span>
                  <select
                    value={reason}
                    onChange={(event) => {
                      setReason(event.target.value as "all" | ReasonCode);
                      setVisiblePeople(10);
                    }}
                  >
                    <option value="all">Все основания</option>
                    {Object.entries(reasons).map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {attention.length === 0 ? (
                <p className="hr-inline-empty">
                  {reason === "all"
                    ? "По выбранному периоду оснований для дополнительной поддержки не найдено."
                    : "С этим основанием сотрудников нет. Выберите другое основание."}
                </p>
              ) : (
                <>
                  <p className="hr-fine-print" aria-live="polite">
                    Показано {count(Math.min(visiblePeople, attention.length))}{" "}
                    из {count(attention.length)} сотрудников с выбранными
                    основаниями.
                  </p>
                  <ul className="hr-support-list">
                    {attention.slice(0, visiblePeople).map((employee) => (
                      <li key={employee.profile.employee_id}>
                        <div className="hr-support-person">
                          <div>
                            <h4>{employee.profile.full_name}</h4>
                            <p className="muted">
                              {employee.profile.department} ·{" "}
                              {employee.profile.role} · {employee.profile.grade}
                            </p>
                          </div>
                          <button
                            className="button button-ghost"
                            onClick={() =>
                              onOpenEmployee(employee.profile.employee_id)
                            }
                            aria-label={`Обсудить развитие: ${employee.profile.full_name}`}
                          >
                            Профиль <ArrowRight size={16} aria-hidden="true" />
                          </button>
                        </div>
                        <ul className="hr-support-reasons">
                          {employee.reasons.map((item) => (
                            <li key={item.code}>
                              <span className="badge">
                                {reasons[item.code]}
                              </span>
                              <p>{item.message}</p>
                            </li>
                          ))}
                        </ul>
                        <p className="hr-fine-print">
                          Последнее завершение в данных:{" "}
                          {employee.last_completed_date
                            ? date(employee.last_completed_date)
                            : "нет записи"}
                          . За период: {count(employee.completed_in_period)}{" "}
                          завершений, {count(employee.no_show_in_period)}{" "}
                          пропусков из{" "}
                          {count(employee.history_records_in_period)} записей.
                        </p>
                      </li>
                    ))}
                  </ul>
                  {visiblePeople < attention.length && (
                    <button
                      className="button button-secondary hr-show-more"
                      onClick={() => setVisiblePeople((value) => value + 10)}
                    >
                      Показать ещё сотрудников
                    </button>
                  )}
                </>
              )}
            </section>

            <section
              className="card hr-analytics-card"
              aria-labelledby="hr-participation-title"
            >
              <h3 id="hr-participation-title">Участие в активностях</h3>
              <p className="muted hr-analytics-description">
                Всего {count(data.history_records_in_period)} записей за
                выбранный период. Повторные назначения учитываются отдельно;
                число участников — уникальные сотрудники каждой активности.
              </p>
              {data.participation.length === 0 ? (
                <p className="hr-inline-empty">
                  В выбранном периоде нет записей об участии.
                </p>
              ) : (
                <div className="hr-participation-list">
                  {data.participation.slice(0, visibleEvents).map((event) => (
                    <details
                      key={event.event_id}
                      className="hr-participation-row"
                    >
                      <summary>
                        <span>
                          <strong>{event.title}</strong>
                          <span className="muted">
                            {event.mandatory
                              ? "Обязательная активность"
                              : "Добровольная активность"}{" "}
                            · {count(event.participant_count)} сотрудников
                          </span>
                        </span>
                        <span className="hr-participation-total">
                          <b>
                            {count(event.completed)} /{" "}
                            {count(event.record_count)}
                          </b>{" "}
                          записей завершены
                        </span>
                      </summary>
                      <dl className="hr-participation-states">
                        {[
                          ["Завершены", event.completed],
                          ["В процессе", event.in_progress],
                          ["Прекращены", event.dropped],
                          ["Пропущены", event.no_show],
                          ["Отклонены", event.declined],
                          ["Просрочены", event.overdue],
                        ].map(([label, value]) => (
                          <div key={label}>
                            <dt>{label}</dt>
                            <dd>{count(Number(value))}</dd>
                          </div>
                        ))}
                      </dl>
                    </details>
                  ))}
                  {visibleEvents < data.participation.length && (
                    <button
                      className="button button-secondary hr-show-more"
                      onClick={() => setVisibleEvents((value) => value + 8)}
                    >
                      Показать ещё активности
                    </button>
                  )}
                </div>
              )}
            </section>

            <aside
              className="hr-analytics-method"
              aria-label="Как читать данные"
            >
              <h3>Как читать этот срез</h3>
              <p>
                Учебных симуляций исключено: {count(data.excluded_simulations)}.{" "}
                Записей с приблизительной исторической датой за период:{" "}
                {count(data.historical_proxy_records_in_period)} из{" "}
                {count(data.history_records_in_period)}. Дата из импортной
                истории не всегда является точным временем завершения.
              </p>
              {data.notes.length > 0 && (
                <ul>
                  {data.notes.map((note, index) => (
                    <li key={index}>{note}</li>
                  ))}
                </ul>
              )}
            </aside>
          </div>
        )}
      </div>
    </section>
  );
}
