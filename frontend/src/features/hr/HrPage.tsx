import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  RefreshCw,
  Search,
  Target,
  Upload,
  Users,
} from "lucide-react";
import type { EmployeeListResponse, HRSummaryResponse } from "../../api";
import { useApp } from "../../context";
import "./hr.css";

const PAGE_SIZE = 20;

type HrPageProps = {
  onOpenEmployee: (id: string) => void;
  onOpenImport: () => void;
};

export function HrPage({ onOpenEmployee, onOpenImport }: HrPageProps) {
  const { client, mode, handleError } = useApp();
  const [summary, setSummary] = useState<HRSummaryResponse | null>(null);
  const [employees, setEmployees] = useState<EmployeeListResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = useCallback(() => setRefresh((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    Promise.all([client.hrSummary(), client.employees(PAGE_SIZE, offset)])
      .then(([nextSummary, nextEmployees]) => {
        if (!active) return;
        setSummary(nextSummary);
        setEmployees(nextEmployees);
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
  }, [client, offset, refresh, handleError]);

  const normalizedQuery = query.trim().toLocaleLowerCase("ru");
  const visibleEmployees =
    employees?.items.filter((employee) =>
      [
        employee.full_name,
        employee.employee_id,
        employee.department,
        employee.role,
        employee.grade,
      ].some((value) =>
        value.toLocaleLowerCase("ru").includes(normalizedQuery),
      ),
    ) ?? [];

  const changePage = (nextOffset: number) => {
    setQuery("");
    setOffset(nextOffset);
  };

  return (
    <div className="hr-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">РАЗВИТИЕ КОМАНДЫ</p>
          <h1>Люди и их следующие шаги</h1>
          <p className="muted">
            Откройте профиль, чтобы увидеть цель, навыки и подходящие активности
            сотрудника.
          </p>
        </div>
        <button className="button button-primary" onClick={onOpenImport}>
          <Upload size={17} aria-hidden="true" /> Загрузить данные
        </button>
      </header>

      {error && (
        <div className="alert hr-error" role="alert">
          <span>{error}</span>
          <button
            className="button button-secondary"
            onClick={reload}
            disabled={loading}
          >
            Повторить
          </button>
        </div>
      )}

      <section
        className="metric-grid hr-metrics"
        aria-label="Сводка по команде"
        aria-busy={loading}
      >
        {[
          {
            label: "Сотрудников",
            value: summary?.employee_count,
            note: "Профили в системе",
            Icon: Users,
          },
          {
            label: "С целью развития",
            value: summary?.employees_with_goal,
            note: "Личная цель или следующая ступень",
            Icon: Target,
          },
          {
            label: "Завершений в истории",
            value: summary?.completion_count,
            note: "Включая учебные симуляции",
            Icon: CheckCheck,
          },
          {
            label: "Учебных симуляций",
            value: summary?.demo_simulation_count,
            note: "Демонстрация роста навыков",
            Icon: FlaskConical,
          },
        ].map(({ label, value, note, Icon }) => (
          <div className="metric-card hr-metric" key={label}>
            <div className="hr-metric-top">
              <span>{label}</span>
              <Icon size={19} aria-hidden="true" />
            </div>
            <strong>
              {value === undefined ? "—" : value.toLocaleString("ru-RU")}
            </strong>
            <span className="muted">{note}</span>
          </div>
        ))}
      </section>

      <section className="card hr-directory" aria-labelledby="team-title">
        <div className="hr-directory-top">
          <div>
            <h2 id="team-title">Команда</h2>
            <p className="muted">
              От общего списка — к конкретному плану развития.
            </p>
          </div>
          <button
            className="button button-ghost"
            onClick={reload}
            disabled={loading}
            aria-label="Обновить список сотрудников"
          >
            <RefreshCw
              size={16}
              className={loading ? "hr-spinning" : ""}
              aria-hidden="true"
            />{" "}
            Обновить
          </button>
        </div>
        <div className="hr-search-row">
          <label className="hr-search">
            <Search size={18} aria-hidden="true" />
            <input
              type="search"
              aria-label="Поиск на текущей странице сотрудников"
              placeholder="Имя, отдел или роль на этой странице"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              disabled={loading || !employees?.items.length}
            />
          </label>
          <span className="muted hr-search-hint">
            Поиск по загруженной странице
          </span>
        </div>

        <div className="hr-table-scroll" aria-busy={loading}>
          <table className="data-table hr-table">
            <caption className="hr-visually-hidden">
              Сотрудники: имя, отдел, роль и грейд. Откройте профиль для
              просмотра развития.
            </caption>
            <thead>
              <tr>
                <th scope="col">Сотрудник</th>
                <th scope="col">Отдел</th>
                <th scope="col">Роль / грейд</th>
                <th scope="col">
                  <span className="hr-visually-hidden">Действие</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={4}>
                    <div className="hr-table-status" role="status">
                      Загружаем данные команды…
                    </div>
                  </td>
                </tr>
              ) : (
                visibleEmployees.map((employee) => (
                  <tr key={employee.employee_id}>
                    <td>
                      <div className="hr-person">
                        <span className="hr-avatar" aria-hidden="true">
                          {employee.full_name
                            .split(/\s+/)
                            .slice(0, 2)
                            .map((part) => part[0])
                            .join("")}
                        </span>
                        <div>
                          <strong>{employee.full_name}</strong>
                          <span className="muted">{employee.employee_id}</span>
                        </div>
                      </div>
                    </td>
                    <td>{employee.department}</td>
                    <td>
                      <div className="hr-role">
                        <span>{employee.role}</span>
                        <span className="badge">{employee.grade}</span>
                      </div>
                    </td>
                    <td className="hr-action-cell">
                      <button
                        className="button button-ghost"
                        onClick={() => onOpenEmployee(employee.employee_id)}
                        aria-label={`Открыть профиль: ${employee.full_name}`}
                      >
                        Профиль <ArrowRight size={16} aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          {!loading && !error && visibleEmployees.length === 0 && (
            <div className="empty-state hr-empty">
              <Users size={30} aria-hidden="true" />
              <h3>
                {normalizedQuery
                  ? "На этой странице совпадений нет"
                  : "Профили ещё не загружены"}
              </h3>
              <p className="muted">
                {normalizedQuery
                  ? "Измените запрос или перейдите на другую страницу."
                  : "Загрузите сотрудников и историю, чтобы начать работу с развитием команды."}
              </p>
              {normalizedQuery ? (
                <button
                  className="button button-secondary"
                  onClick={() => setQuery("")}
                >
                  Сбросить поиск
                </button>
              ) : (
                <button
                  className="button button-primary"
                  onClick={onOpenImport}
                >
                  Загрузить профили
                </button>
              )}
            </div>
          )}
        </div>

        <div className="hr-pagination">
          <span className="muted" aria-live="polite">
            {employees
              ? normalizedQuery
                ? `Найдено ${visibleEmployees.length} из ${employees.items.length} на странице`
                : employees.total === 0
                  ? "Нет сотрудников"
                  : `${employees.offset + 1}–${Math.min(employees.offset + employees.items.length, employees.total)} из ${employees.total} сотрудников`
              : "Ожидаем данные"}
          </span>
          <div>
            <button
              className="button button-secondary hr-page-button"
              onClick={() => changePage(Math.max(0, offset - PAGE_SIZE))}
              disabled={loading || offset === 0}
              aria-label="Предыдущая страница"
            >
              <ChevronLeft size={18} aria-hidden="true" />
            </button>
            <button
              className="button button-secondary hr-page-button"
              onClick={() => changePage(offset + PAGE_SIZE)}
              disabled={
                loading ||
                !employees ||
                employees.offset + employees.items.length >= employees.total
              }
              aria-label="Следующая страница"
            >
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          </div>
        </div>
      </section>

      <aside className="hr-info-note">
        <span className="hr-info-icon">
          <Target size={20} aria-hidden="true" />
        </span>
        <div>
          <h3>Развитие видно в каждом профиле</h3>
          <p>
            Навыки, требования цели и история доступны в карточке сотрудника.
            Общая аналитика пробелов и участия пока не подключена.
            {mode === "demo"
              ? " Сейчас показана вымышленная команда для знакомства с продуктом."
              : ""}
          </p>
        </div>
      </aside>
    </div>
  );
}
