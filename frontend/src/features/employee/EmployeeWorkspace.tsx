import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Flag,
  History,
  Layers3,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
} from "lucide-react";
import { ApiError } from "../../api/client";
import type {
  CatalogEvent,
  CompletionRequest,
  CompletionResponse,
  EmployeeDetailResponse,
  Goal,
  PreviewResponse,
  RecommendationResponse,
  RecommendedEvent,
} from "../../api/types";
import { useApp } from "../../context";
import {
  Dialog,
  EmptyState,
  ErrorAlert,
  Loading,
  formatDate,
  formatNumber,
} from "../../components/ui";

export type EmployeeView =
  "overview" | "skills" | "recommendations" | "catalog" | "history";
const formats: Record<string, string> = {
  self_paced: "В своём темпе",
  online: "Онлайн",
  offline: "Очно",
};
const eventTypes: Record<string, string> = {
  course: "Курс",
  workshop: "Практикум",
  mentoring: "Менторство",
  meetup: "Встреча",
  certification: "Сертификация",
  compliance: "Обязательная программа",
  onboarding: "Адаптация",
};
const statuses: Record<string, string> = {
  completed: "Завершено",
  in_progress: "В процессе",
  dropped: "Прервано",
  no_show: "Не состоялось участие",
  declined: "Отказ",
  overdue: "Срок прошёл",
};
const recommendationStates: Record<string, [string, string]> = {
  no_target: [
    "Сначала выберите направление",
    "У вас пока нет карьерной цели. Выберите роль и грейд, к которым хотите двигаться.",
  ],
  no_candidates: [
    "Сейчас нет подходящего следующего шага",
    "В текущем каталоге нет полезных доступных активностей для вашей цели. Обсудите варианты развития с HR.",
  ],
  not_configured: [
    "Подбор рекомендаций ещё не подключён",
    "Ваш профиль, история и прогресс доступны. Персональные рекомендации появятся после подключения сервиса.",
  ],
  unavailable: [
    "Не получилось подобрать рекомендации",
    "Сервис рекомендаций временно недоступен. Попробуйте ещё раз немного позже — ваш прогресс сохранён.",
  ],
};

export default function EmployeeWorkspace({
  employeeId,
  view,
  navigate,
  isHr = false,
}: {
  employeeId: string;
  view: EmployeeView;
  navigate: (view: EmployeeView) => void;
  isHr?: boolean;
}) {
  const { client, catalog, mode, user, handleError } = useApp();
  const [profile, setProfile] = useState<EmployeeDetailResponse | null>(null);
  const [recommendations, setRecommendations] =
    useState<RecommendationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [recError, setRecError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [goalOpen, setGoalOpen] = useState(false);
  const [selected, setSelected] = useState<{
    event: CatalogEvent;
    recordId?: string;
  } | null>(null);
  const [result, setResult] = useState<{
    value: CompletionResponse;
    before: number | null;
    after: number | null;
  } | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const requestVersion = useRef(0);
  const recommendationVersion = useRef(0);
  const profileRef = useRef<EmployeeDetailResponse | null>(null);
  const restoredPendingScope = useRef("");

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    const recommendationRequest = ++recommendationVersion.current;
    setLoading(true);
    setError("");
    setRecError("");
    setResult(null);
    setGenerating(false);
    try {
      const value = await client.employee(employeeId);
      if (version !== requestVersion.current) return;
      profileRef.current = value;
      setProfile(value);
      setRecommendations(null);
      setLoading(false);
      try {
        const latest = await client.latest(employeeId);
        if (
          version === requestVersion.current &&
          recommendationRequest === recommendationVersion.current
        ) {
          setRecommendations({
            ...latest,
            stale:
              latest.stale ||
              latest.state_version !== profileRef.current?.state_version,
          });
        }
      } catch (e) {
        if (
          version === requestVersion.current &&
          recommendationRequest === recommendationVersion.current &&
          !(e instanceof ApiError && e.status === 404)
        )
          setRecError(handleError(e));
      }
    } catch (e) {
      if (version === requestVersion.current) {
        setError(handleError(e));
        setLoading(false);
      }
    }
  }, [client, employeeId, handleError]);
  useEffect(() => {
    void refresh();
    return () => {
      requestVersion.current++;
      recommendationVersion.current++;
    };
  }, [refresh]);
  useEffect(() => {
    setSearch("");
    setFilter("all");
  }, [view]);
  useEffect(() => {
    if (
      !profile ||
      profile.profile.employee_id !== employeeId ||
      !catalog?.events
    )
      return;
    const scope = `${mode}:${user.id}:${employeeId}`;
    if (restoredPendingScope.current === scope) return;
    restoredPendingScope.current = scope;
    const candidates: { event: CatalogEvent; recordId?: string }[] =
      catalog.events.map((event) => ({ event }));
    for (const record of profile.history) {
      const event = catalog.events.find(
        (item) => item.event_id === record.event_id,
      );
      if (event) candidates.push({ event, recordId: record.record_id });
    }
    const saved = candidates.find((item) =>
      readPending(
        pendingScope(
          mode,
          user.id,
          employeeId,
          item.event.event_id,
          item.recordId,
        ),
      ),
    );
    if (saved) setSelected(saved);
  }, [profile, catalog, mode, user.id, employeeId]);

  async function generate() {
    if (!profile || generating) return;
    const version = requestVersion.current;
    const generation = ++recommendationVersion.current;
    const active = () =>
      version === requestVersion.current &&
      generation === recommendationVersion.current;
    setGenerating(true);
    setRecError("");
    try {
      const value = await client.recommendations(
        employeeId,
        profile.scenario_date,
      );
      if (active())
        setRecommendations({
          ...value,
          stale:
            value.stale ||
            value.state_version !== profileRef.current?.state_version,
        });
    } catch (e) {
      if (!active()) return;
      setRecError(handleError(e));
      if (e instanceof ApiError && e.status === 409) await refresh();
    } finally {
      if (active()) setGenerating(false);
    }
  }
  const skillName = (id: string) =>
    catalog?.skills.find((s) => s.skill_id === id)?.name ?? id;
  const getEvent = (id: string) =>
    catalog?.events?.find((e) => e.event_id === id);

  if (loading) return <Loading />;
  if (!profile)
    return (
      <>
        <ErrorAlert
          message={error || "Не удалось загрузить профиль."}
          retry={() => void refresh()}
        />
        <EmptyState
          title="Профиль пока недоступен"
          text="Проверьте подключение или обратитесь к HR, чтобы загрузить данные сотрудника."
        />
      </>
    );
  const person = profile.profile;
  const progress = profile.progress;
  const goal = profile.goal;
  const roleProfile = catalog?.role_profiles.find(
    (r) => r.role === goal?.target_role && r.grade === goal?.target_grade,
  );
  const firstName = person.full_name.split(" ")[0];
  const title = {
    overview: "Моё развитие",
    skills: "Навыки и цель",
    recommendations: "Следующий шаг",
    catalog: "Каталог активностей",
    history: "История развития",
  }[view];
  const descriptions = {
    overview: `${person.full_name} · ${person.role} · ${person.grade}`,
    skills: "Что уже получается и на чём стоит сосредоточиться.",
    recommendations:
      "Подходящие активности и понятное объяснение пользы каждой.",
    catalog:
      "Изучайте возможности. Доступность и результат проверим перед действием.",
    history: "Ваш путь: пройденные активности, назначения и изменения навыков.",
  };
  const historyCompleted = profile.history.filter(
    (h) => h.status === "completed",
  ).length;

  function recommendationCard(rec: RecommendedEvent, index: number) {
    const event = getEvent(rec.event_id);
    const stale = recommendations?.stale;
    return (
      <article
        className={`recommendation-card ${index === 0 ? "recommended-first" : ""}`}
        key={rec.event_id}
      >
        <div className="rec-top">
          <span className={`event-icon tone-${index % 3}`}>
            <BookOpen size={23} />
          </span>
          <span className={index === 0 ? "badge badge-green" : "badge"}>
            {stale
              ? "Нужен пересчёт"
              : index === 0
                ? "С чего начать"
                : "Ещё один путь"}
          </span>
        </div>
        <p className="eyebrow">{eventTypes[event?.type ?? "course"]}</p>
        <h3>{rec.title}</h3>
        <div className="event-meta">
          <span>
            <Clock3 size={14} />
            {event
              ? `${formatNumber(event.duration_hours)} ч`
              : "Длительность не указана"}
          </span>
          <span>{event ? formats[event.format] : "Формат не указан"}</span>
        </div>
        <div className="effect-chips">
          {rec.effects.map((effect) => (
            <span className="effect-chip" key={effect.skill_id}>
              {skillName(effect.skill_id)}{" "}
              <strong>+{formatNumber(effect.delta)}</strong>
            </span>
          ))}
        </div>
        <details className="why-details" open={index === 0 && !stale}>
          <summary>
            <Sparkles size={15} />
            Почему вам подходит
            <ChevronRight size={15} />
          </summary>
          <p>{rec.explanation.text}</p>
        </details>
        <button
          className={`button ${index === 0 ? "button-primary" : "button-secondary"} button-full`}
          disabled={!event || stale || generating}
          onClick={() => event && setSelected({ event })}
        >
          Посмотреть результат
          <ArrowUpRight size={17} />
        </button>
      </article>
    );
  }

  const recommendationsSection = (
    <section className="section-block" aria-labelledby="recommendations-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">ИЗ ЦЕЛИ — В ДЕЙСТВИЕ</p>
          <h2 id="recommendations-title">
            Ваш следующий шаг{" "}
            <span className="small-ai">
              <Sparkles size={13} />
              {mode === "demo" ? "Пример" : "AI"}
            </span>
          </h2>
        </div>
        <button
          className="button button-ghost"
          disabled={generating}
          onClick={() => void generate()}
        >
          <RefreshCw size={16} className={generating ? "spin" : ""} />
          {generating
            ? "Подбираем…"
            : recommendations
              ? "Обновить"
              : "Подобрать шаги"}
        </button>
      </div>
      <p className="section-description">
        Учитываем вашу цель, текущие навыки и историю участия.
      </p>
      {recError && <ErrorAlert message={recError} />}
      {recommendations?.stale && (
        <div className="alert alert-warning" role="status">
          Ваш профиль изменился. Обновите подборку, чтобы увидеть актуальные
          шаги.
        </div>
      )}
      {generating ? (
        <Loading text="Подбираем шаги к вашей цели. Обычно это занимает до 10 секунд…" />
      ) : recommendations?.status === "ok" ? (
        <div className="recommendation-grid">
          {recommendations.recommendations.map(recommendationCard)}
        </div>
      ) : recommendations ? (
        <EmptyState
          title={recommendationStates[recommendations.status][0]}
          text={recommendationStates[recommendations.status][1]}
        >
          {recommendations.status === "no_target" && (
            <button
              className="button button-primary"
              onClick={() => setGoalOpen(true)}
            >
              Выбрать цель
              <ArrowRight size={17} />
            </button>
          )}
          {recommendations.status === "unavailable" && (
            <button
              className="button button-secondary"
              onClick={() => void generate()}
            >
              Попробовать снова
            </button>
          )}
        </EmptyState>
      ) : (
        <div className="first-step-card">
          <span className="feature-icon">
            <Sparkles size={28} />
          </span>
          <div>
            <h3>Найдём полезный шаг именно для вас</h3>
            <p>
              Получите до трёх рекомендаций с объяснением, как каждая помогает
              вашей цели.
            </p>
          </div>
          <button
            className="button button-primary"
            onClick={() => void generate()}
          >
            Подобрать шаги
            <ArrowRight size={17} />
          </button>
        </div>
      )}
    </section>
  );

  return (
    <div className="employee-workspace">
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            {isHr
              ? `ПРОФИЛЬ СОТРУДНИКА · ${person.full_name}`
              : "ВАШ ПУТЬ, ВАШ ТЕМП"}
          </p>
          <h1>{title}</h1>
          <p>{descriptions[view]}</p>
        </div>
        <span className="date-pill">
          <span className="status-dot" />
          Данные на {formatDate(profile.scenario_date)}
        </span>
      </div>
      {error && <ErrorAlert message={error} />}
      {result && (
        <div className="completion-success" role="status">
          <span className="success-icon">
            <CheckCircle2 size={25} />
          </span>
          <div>
            <h3>
              {result.value.completion.mode === "demo_simulation"
                ? "Симуляция сохранена отдельно"
                : "Ещё один шаг к вашей цели!"}
            </h3>
            <p>
              {result.value.completion.effects
                .map(
                  (e) =>
                    `${skillName(e.skill_id)}: ${formatNumber(e.before)} → ${formatNumber(e.after)}`,
                )
                .join(" · ") || "Активность сохранена в истории."}
            </p>
            {result.before !== null && result.after !== null && (
              <p>
                Соответствие цели: {formatNumber(result.before)}% →{" "}
                <strong>{formatNumber(result.after)}%</strong>
              </p>
            )}
          </div>
          <button
            className="icon-button"
            aria-label="Скрыть результат"
            onClick={() => setResult(null)}
          >
            <Check size={20} />
          </button>
        </div>
      )}
      {(view === "overview" || view === "skills") && (
        <>
          <section className="goal-hero" aria-labelledby="goal-title">
            <div className="goal-hero-content">
              <span className="hero-kicker">
                <Flag size={15} />
                ВАША КАРЬЕРНАЯ ЦЕЛЬ
              </span>
              <h2 id="goal-title">
                {goal ? (
                  <>
                    {goal.target_grade}
                    <br />
                    <span>{goal.target_role}</span>
                  </>
                ) : (
                  <>
                    Куда вы хотите
                    <br />
                    <span>двигаться дальше?</span>
                  </>
                )}
              </h2>
              <p>
                {goal
                  ? "Развивайте нужные навыки. Следующий шаг уже ближе."
                  : "Выберите направление — и мы покажем путь к нему."}
              </p>
              <button
                className="button hero-button"
                onClick={() => setGoalOpen(true)}
              >
                {goal ? "Изменить цель" : "Выбрать цель"}
                <ArrowUpRight size={17} />
              </button>
            </div>
            <div className="hero-progress">
              <div className="progress-orbit">
                <svg viewBox="0 0 180 180" aria-hidden="true">
                  <circle className="orbit-track" cx="90" cy="90" r="77" />
                  <circle
                    className="orbit-value"
                    cx="90"
                    cy="90"
                    r="77"
                    strokeDasharray={`${(progress?.percent ?? 0) * 4.838} 483.81`}
                  />
                </svg>
                <div>
                  <strong>
                    {progress ? Math.round(progress.percent) : "—"}
                    {progress && <span>%</span>}
                  </strong>
                  <span>соответствие цели</span>
                </div>
              </div>
              <span className="hero-progress-caption">
                {progress
                  ? `${progress.met_skills} из ${progress.total_skills} требований выполнено`
                  : "Ваша цель ещё не выбрана"}
              </span>
            </div>
            <div className="hero-decoration" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          </section>
          <div className="journey-strip">
            <span>
              <span className="journey-dot filled">
                <Check size={12} />
              </span>
              <span className="muted">Сейчас</span>
              <strong>{person.grade}</strong>
            </span>
            <span className="journey-line" />
            <span>
              <span className="journey-dot active" />
              <strong>Развиваем навыки</strong>
            </span>
            <span className="journey-line" />
            <span>
              <span className="journey-dot">
                <Flag size={12} />
              </span>
              <span className="muted">Цель</span>
              <strong>{goal?.target_grade ?? "Не выбрана"}</strong>
            </span>
          </div>
          <div className="metric-grid">
            <article className="metric-card">
              <span className="metric-icon">
                <Target size={21} />
              </span>
              <div>
                <span>Навыки на уровне цели</span>
                <strong>
                  {progress ? `${progress.met_skills}` : "—"}
                  {progress && <small> / {progress.total_skills}</small>}
                </strong>
              </div>
            </article>
            <article className="metric-card">
              <span className="metric-icon purple">
                <Layers3 size={21} />
              </span>
              <div>
                <span>Ключевые требования</span>
                <strong>
                  {progress ? progress.critical_met : "—"}
                  {progress && <small> / {progress.critical_total}</small>}
                </strong>
              </div>
            </article>
            <article className="metric-card">
              <span className="metric-icon amber">
                <CheckCircle2 size={21} />
              </span>
              <div>
                <span>Завершено активностей</span>
                <strong>
                  {historyCompleted}
                  <small> в истории</small>
                </strong>
              </div>
            </article>
          </div>
        </>
      )}
      {(view === "overview" || view === "recommendations") &&
        recommendationsSection}
      {(view === "overview" || view === "skills") && (
        <section className="section-block">
          <div className="section-heading">
            <div>
              <p className="eyebrow">ФОКУС РАЗВИТИЯ</p>
              <h2>
                {view === "skills" ? "Ваши навыки" : "Что приблизит вас к цели"}
              </h2>
            </div>
            {view === "overview" && (
              <button
                className="button button-ghost"
                onClick={() => navigate("skills")}
              >
                Все навыки
                <ArrowRight size={16} />
              </button>
            )}
          </div>
          <div className="skills-panel card">
            <div className="skill-table-heading">
              <span>Навык</span>
              <span>Текущий уровень → цель</span>
            </div>
            {(view === "overview"
              ? profile.gaps
                  .filter((g) => g.gap > 0)
                  .slice(0, 4)
                  .map((g) => ({
                    skill_id: g.skill_id,
                    level: g.current_level,
                  }))
              : profile.current_skills
            ).map((skill) => {
              const gap = profile.gaps.find(
                (g) => g.skill_id === skill.skill_id,
              );
              const critical = roleProfile?.critical_skills.includes(
                skill.skill_id,
              );
              return (
                <div className="skill-row" key={skill.skill_id}>
                  <div>
                    <strong>{skillName(skill.skill_id)}</strong>
                    {critical && (
                      <span className="skill-priority">Ключевой для цели</span>
                    )}
                  </div>
                  <div className="skill-level-display">
                    <div
                      className="skill-blocks"
                      aria-label={`Текущий уровень ${skill.level} из 5${gap ? `, цель ${gap.target_level}` : ""}`}
                    >
                      {[1, 2, 3, 4, 5].map((n) => (
                        <span
                          className={
                            gap && n - 1 < gap.target_level ? "needed" : ""
                          }
                          key={n}
                        >
                          <span
                            style={{
                              width: `${Math.min(1, Math.max(0, skill.level - n + 1)) * 100}%`,
                            }}
                          />
                        </span>
                      ))}
                    </div>
                    <span className="skill-numbers">
                      {formatNumber(skill.level)}
                      {gap && (
                        <>
                          <ArrowRight size={13} />
                          <strong>{formatNumber(gap.target_level)}</strong>
                        </>
                      )}
                    </span>
                  </div>
                </div>
              );
            })}
            {view === "overview" && !profile.gaps.some((g) => g.gap > 0) && (
              <EmptyState
                title={
                  goal ? "Требования цели выполнены" : "Выберите цель развития"
                }
                text={
                  goal
                    ? "Обсудите следующий карьерный шаг с руководителем. Соответствие навыков не означает автоматического повышения."
                    : "После выбора появятся навыки, на которых стоит сосредоточиться."
                }
              />
            )}
            <div className="skills-legend">
              <span>
                <i className="legend-earned" />
                Текущий уровень
              </span>
              <span>
                <i className="legend-needed" />
                До уровня цели
              </span>
              <span>Шкала 0–5</span>
            </div>
          </div>
          <p className="footnote">
            <ShieldCheck size={14} />
            Прогресс показывает соответствие навыков выбранной цели и не
            гарантирует повышение.
          </p>
        </section>
      )}
      {view === "catalog" && (
        <section className="section-block">
          <div className="catalog-toolbar">
            <label className="search-field">
              <Search size={18} />
              <input
                aria-label="Поиск активностей"
                placeholder="Название или описание активности"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <select
              aria-label="Формат активности"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            >
              <option value="all">Все форматы</option>
              <option value="self_paced">В своём темпе</option>
              <option value="online">Онлайн</option>
              <option value="offline">Очно</option>
            </select>
          </div>
          <div className="catalog-grid">
            {catalog?.events
              ?.filter(
                (e) =>
                  (filter === "all" || e.format === filter) &&
                  `${e.title} ${e.description}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
              )
              .map((event, i) => (
                <article className="catalog-card card" key={event.event_id}>
                  <span className={`event-icon tone-${i % 3}`}>
                    <BookOpen size={23} />
                  </span>
                  <span className="eyebrow">{eventTypes[event.type]}</span>
                  <h3>{event.title}</h3>
                  <p>{event.description}</p>
                  <div className="event-meta">
                    <span>
                      <Clock3 size={14} />
                      {formatNumber(event.duration_hours)} ч
                    </span>
                    <span>{formats[event.format]}</span>
                  </div>
                  {event.mandatory ? (
                    <span className="badge">По назначению · см. историю</span>
                  ) : (
                    <button
                      className="button button-secondary button-full"
                      onClick={() => setSelected({ event })}
                    >
                      Проверить для моей цели
                      <ArrowUpRight size={16} />
                    </button>
                  )}
                </article>
              ))}
          </div>
          {!catalog?.events?.some(
            (e) =>
              (filter === "all" || e.format === filter) &&
              `${e.title} ${e.description}`
                .toLowerCase()
                .includes(search.toLowerCase()),
          ) && (
            <EmptyState
              title="Активности не найдены"
              text="Попробуйте другой запрос или формат. Если каталог пуст, обратитесь к HR."
            />
          )}
        </section>
      )}
      {view === "history" && (
        <section className="section-block">
          <div className="section-heading">
            <h2>Каждый шаг имеет значение</h2>
            <span className="badge">Записей: {profile.history.length}</span>
          </div>
          {profile.history.length === 0 ? (
            <EmptyState
              title="Ваш путь только начинается"
              text="Здесь появятся назначения и завершённые активности. Начните с подходящего следующего шага."
            />
          ) : (
            <div className="history-list card">
              {[...profile.history]
                .sort((a, b) => b.activity_date.localeCompare(a.activity_date))
                .map((record) => (
                  <article className="history-row" key={record.record_id}>
                    <span
                      className={`history-icon ${record.status === "completed" ? "completed" : ""}`}
                    >
                      {record.status === "completed" ? (
                        <Check size={20} />
                      ) : (
                        <History size={20} />
                      )}
                    </span>
                    <div className="history-detail">
                      <h3>
                        {getEvent(record.event_id)?.title ?? record.event_id}
                      </h3>
                      <p>
                        {formatDate(record.activity_date)} ·{" "}
                        {record.date_source === "historical_proxy"
                          ? "Дата из импортированной истории"
                          : "Дата завершения"}
                        {record.mode === "demo_simulation" && " · Симуляция"}
                      </p>
                      {(record.effects ?? []).length > 0 && (
                        <div className="effect-chips">
                          {(record.effects ?? []).map((e) => (
                            <span className="effect-chip" key={e.skill_id}>
                              {skillName(e.skill_id)} {formatNumber(e.before)} →{" "}
                              {formatNumber(e.after)}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="history-actions">
                      <span
                        className={`badge ${record.status === "completed" ? "badge-green" : ""}`}
                      >
                        {statuses[record.status]}
                      </span>
                      {(record.status === "in_progress" ||
                        record.status === "overdue") &&
                        getEvent(record.event_id) && (
                          <button
                            className="button button-secondary button-small"
                            onClick={() =>
                              setSelected({
                                event: getEvent(record.event_id)!,
                                recordId: record.record_id,
                              })
                            }
                          >
                            Завершить
                          </button>
                        )}
                    </div>
                  </article>
                ))}
            </div>
          )}
        </section>
      )}
      {view === "overview" && (
        <div className="gentle-banner">
          <span>
            <TrendingUp size={21} />
          </span>
          <div>
            <strong>Развитие — это личный путь</strong>
            <p>
              Двигайтесь в удобном темпе. Ваш прогресс виден вам и HR, а не
              другим сотрудникам.
            </p>
          </div>
          <button
            className="button button-ghost"
            onClick={() => navigate("history")}
          >
            Моя история
            <ArrowRight size={16} />
          </button>
        </div>
      )}
      {goalOpen && (
        <GoalDialog
          profile={profile}
          onClose={() => setGoalOpen(false)}
          onRefresh={() => {
            setGoalOpen(false);
            void refresh();
          }}
          onSaved={(value) => {
            recommendationVersion.current++;
            setGenerating(false);
            profileRef.current = value;
            setProfile(value);
            setRecommendations((r) => (r ? { ...r, stale: true } : null));
            setGoalOpen(false);
          }}
        />
      )}
      {selected && (
        <ActivityDialog
          event={selected.event}
          recordId={selected.recordId}
          profile={profile}
          onClose={() => setSelected(null)}
          onRefresh={() => {
            setSelected(null);
            void refresh();
          }}
          onCompleted={async (value) => {
            const version = requestVersion.current;
            const before = profile.progress?.percent ?? null;
            recommendationVersion.current++;
            setGenerating(false);
            setSelected(null);
            setRecommendations((r) => (r ? { ...r, stale: true } : null));
            try {
              const latestProfile = await client.employee(employeeId);
              if (version !== requestVersion.current) return;
              profileRef.current = latestProfile;
              setProfile(latestProfile);
              setResult({
                value,
                before,
                after: latestProfile.progress?.percent ?? null,
              });
            } catch (e) {
              if (version !== requestVersion.current) return;
              setResult({ value, before, after: null });
              setError(`Активность сохранена. ${handleError(e)}`);
            }
          }}
        />
      )}
    </div>
  );
}

type PendingCompletion = { body: CompletionRequest; key: string };
const pendingPrefix = "career-quest.pending-completion.v1:";
const pendingMemory = new Map<string, PendingCompletion>();
function pendingScope(
  mode: string,
  userId: string,
  employeeId: string,
  eventId: string,
  recordId?: string,
) {
  return (
    pendingPrefix +
    encodeURIComponent(
      JSON.stringify([mode, userId, employeeId, eventId, recordId ?? null]),
    )
  );
}
function writePending(scope: string, command: PendingCompletion) {
  pendingMemory.set(scope, command);
  try {
    sessionStorage.setItem(scope, JSON.stringify(command));
  } catch {
    /* Same-page retry remains available when storage is disabled. */
  }
}
function clearPending(scope: string) {
  pendingMemory.delete(scope);
  try {
    sessionStorage.removeItem(scope);
  } catch {
    /* Storage may be disabled. */
  }
}
function readPending(scope: string): PendingCompletion | null {
  let serialized: string | null;
  try {
    serialized = sessionStorage.getItem(scope);
  } catch {
    return pendingMemory.get(scope) ?? null;
  }
  if (!serialized) return null;
  try {
    const value = JSON.parse(serialized) as PendingCompletion;
    const [, , , eventId, recordId] = JSON.parse(
      decodeURIComponent(scope.slice(pendingPrefix.length)),
    ) as unknown[];
    const body = value.body;
    if (
      typeof value.key !== "string" ||
      !/^[\x21-\x7e]{1,128}$/.test(value.key) ||
      !body ||
      !Number.isInteger(body.expected_state_version) ||
      body.expected_state_version < 0 ||
      body.event_id !== eventId ||
      (body.record_id ?? null) !== recordId ||
      !["completion", "demo_simulation"].includes(body.mode)
    )
      return null;
    // Store only the command metadata needed for safe replay; never profile data,
    // uploaded source files, passwords, or the session cookie.
    return {
      key: value.key,
      body: {
        expected_state_version: body.expected_state_version,
        event_id: body.event_id,
        mode: body.mode,
        ...(body.record_id ? { record_id: body.record_id } : {}),
      },
    };
  } catch {
    return null;
  }
}

function GoalDialog({
  profile,
  onClose,
  onRefresh,
  onSaved,
}: {
  profile: EmployeeDetailResponse;
  onClose: () => void;
  onRefresh: () => void;
  onSaved: (value: EmployeeDetailResponse) => void;
}) {
  const { client, catalog, handleError } = useApp();
  const [selected, setSelected] = useState(
    profile.goal
      ? `${profile.goal.target_role}|${profile.goal.target_grade}`
      : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const operation = useRef(0);
  useEffect(() => {
    operation.current++;
    return () => {
      operation.current++;
    };
  }, [client, profile.profile.employee_id]);

  async function save() {
    if (!selected || busy || needsRefresh) return;
    const choice = catalog?.role_profiles.find(
      (r) => `${r.role}|${r.grade}` === selected,
    );
    if (!choice) return;
    const version = operation.current;
    setBusy(true);
    setError("");
    try {
      const value = await client.goal(profile.profile.employee_id, {
        expected_state_version: profile.state_version,
        goal: { target_role: choice.role, target_grade: choice.grade } as Goal,
      });
      if (version === operation.current) onSaved(value);
    } catch (e) {
      if (version !== operation.current) return;
      setError(handleError(e));
      setNeedsRefresh(e instanceof ApiError && e.status === 409);
    } finally {
      if (version === operation.current) setBusy(false);
    }
  }

  return (
    <Dialog title="Куда вы хотите расти?" onClose={onClose} busy={busy}>
      <p className="modal-description">
        Выберите направление. Мы покажем требования к навыкам и подходящие шаги
        развития.
      </p>
      <label className="field-label" htmlFor="career-goal">
        Карьерная цель
      </label>
      <select
        id="career-goal"
        className="field-input"
        value={selected}
        disabled={busy || needsRefresh}
        onChange={(e) => setSelected(e.target.value)}
      >
        <option value="">Выберите роль и грейд</option>
        {catalog?.role_profiles.map((r) => (
          <option key={`${r.role}|${r.grade}`} value={`${r.role}|${r.grade}`}>
            {r.grade} · {r.role}
          </option>
        ))}
      </select>
      <p className="field-hint">
        Цель помогает планировать развитие. Она не меняет вашу должность или
        права доступа.
      </p>
      {error && <ErrorAlert message={error} />}
      <div className="modal-actions">
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={onClose}
        >
          Отмена
        </button>
        {needsRefresh ? (
          <button className="button button-primary" onClick={onRefresh}>
            Обновить профиль
            <RefreshCw size={17} />
          </button>
        ) : (
          <button
            className="button button-primary"
            disabled={busy || !selected}
            onClick={() => void save()}
          >
            {busy ? "Сохраняем…" : "Сохранить цель"}
            <ArrowRight size={17} />
          </button>
        )}
      </div>
    </Dialog>
  );
}

function ActivityDialog({
  event,
  recordId,
  profile,
  onClose,
  onRefresh,
  onCompleted,
}: {
  event: CatalogEvent;
  recordId?: string;
  profile: EmployeeDetailResponse;
  onClose: () => void;
  onRefresh: () => void;
  onCompleted: (value: CompletionResponse) => Promise<void>;
}) {
  const { client, catalog, mode, user, handleError } = useApp();
  const storageScope = pendingScope(
    mode,
    user.id,
    profile.profile.employee_id,
    event.event_id,
    recordId,
  );
  const [pending, setPending] = useState<PendingCompletion | null>(() =>
    readPending(storageScope),
  );
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [checking, setChecking] = useState(!recordId && !pending);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [simulation, setSimulation] = useState(
    pending?.body.mode === "demo_simulation",
  );
  const [confirmed, setConfirmed] = useState(!!pending);
  const [uncertain, setUncertain] = useState(!!pending);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const operation = useRef(0);
  useEffect(() => {
    operation.current++;
    return () => {
      operation.current++;
    };
  }, [client, storageScope]);

  useEffect(() => {
    // Replaying a saved command must precede a fresh preview/revision check: the
    // original completion may already have changed the server's profile version.
    if (recordId || pending) return;
    let active = true;
    client
      .preview(profile.profile.employee_id, {
        expected_state_version: profile.state_version,
        scenario_date: profile.scenario_date,
        event_ids: [event.event_id],
      })
      .then((value) => {
        if (active) setPreview(value);
      })
      .catch((e) => {
        if (active) {
          setError(handleError(e));
          setNeedsRefresh(e instanceof ApiError && e.status === 409);
        }
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, [client, event.event_id, profile, recordId, pending, handleError]);

  async function complete() {
    if (
      busy ||
      needsRefresh ||
      !confirmed ||
      (!preview && !recordId && !pending)
    )
      return;
    const version = operation.current;
    const command = pending ?? {
      key: crypto.randomUUID(),
      body: {
        expected_state_version: profile.state_version,
        event_id: event.event_id,
        mode: simulation
          ? ("demo_simulation" as const)
          : ("completion" as const),
        ...(recordId ? { record_id: recordId } : {}),
      },
    };
    writePending(storageScope, command);
    setPending(command);
    setBusy(true);
    setError("");
    try {
      const value = await client.complete(
        profile.profile.employee_id,
        command.body,
        command.key,
      );
      clearPending(storageScope);
      if (version !== operation.current) return;
      setUncertain(false);
      setPending(null);
      await onCompleted(value);
    } catch (e) {
      const isUncertain =
        !(e instanceof ApiError) ||
        e.status === 0 ||
        e.status >= 500 ||
        e.code === "INVALID_RESPONSE";
      if (!isUncertain) clearPending(storageScope);
      if (version !== operation.current) return;
      setError(handleError(e));
      setUncertain(isUncertain);
      setNeedsRefresh(e instanceof ApiError && e.status === 409);
      if (!isUncertain) setPending(null);
    } finally {
      if (version === operation.current) setBusy(false);
    }
  }

  return (
    <Dialog title={event.title} onClose={onClose} busy={busy}>
      <div className="event-meta">
        <span>
          <Clock3 size={15} />
          {formatNumber(event.duration_hours)} ч
        </span>
        <span>{formats[event.format]}</span>
      </div>
      <p className="modal-description">{event.description}</p>
      {checking && (
        <Loading text="Проверяем доступность и ожидаемый результат…" />
      )}
      {preview && (
        <div className="preview-panel">
          <span className="eyebrow">ЧТО ДАСТ ЭТА АКТИВНОСТЬ</span>
          {preview.effects.map((effect) => (
            <div className="preview-effect" key={effect.skill_id}>
              <span>
                {catalog?.skills.find((s) => s.skill_id === effect.skill_id)
                  ?.name ?? effect.skill_id}
              </span>
              <span>
                {formatNumber(effect.before)}
                <ArrowRight size={15} />
                <strong>{formatNumber(effect.after)}</strong>
                <span className="effect-chip">
                  +{formatNumber(effect.delta)}
                </span>
              </span>
            </div>
          ))}
          <p className="field-hint">
            Это предварительный расчёт. Ваши навыки пока не изменились.
          </p>
        </div>
      )}
      {error && <ErrorAlert message={error} />}
      {(preview || recordId || pending) && (
        <>
          <label className="check-field">
            <input
              type="checkbox"
              checked={simulation}
              disabled={busy || !!pending}
              onChange={(e) => {
                setSimulation(e.target.checked);
                setConfirmed(false);
              }}
            />
            <span>
              <strong>Симуляция результата</strong>
              <small>
                Посмотреть изменение прогресса в демонстрации. Запись будет явно
                помечена как симуляция.
              </small>
            </span>
          </label>
          <label className="check-field confirmation">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={busy || !!pending}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>
              {simulation
                ? "Понимаю: симуляция будет сохранена отдельно в истории"
                : mode === "demo"
                  ? "Завершить активность в учебном примере"
                  : "Подтверждаю, что действительно завершил(а) активность"}
            </span>
          </label>
        </>
      )}
      {uncertain && (
        <p className="field-hint">
          Ответ не получен, но действие могло сохраниться. Повтор отправит ту же
          операцию и не начислит прогресс второй раз. Можно закрыть окно и
          вернуться к подтверждению.
        </p>
      )}
      <div className="modal-actions">
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={onClose}
        >
          Закрыть
        </button>
        {needsRefresh ? (
          <button
            className="button button-primary"
            disabled={busy}
            onClick={onRefresh}
          >
            Обновить профиль
            <RefreshCw size={17} />
          </button>
        ) : (
          <button
            className="button button-primary"
            disabled={
              busy ||
              checking ||
              !confirmed ||
              (!preview && !recordId && !pending)
            }
            onClick={() => void complete()}
          >
            {busy
              ? "Сохраняем…"
              : uncertain
                ? "Повторить подтверждение"
                : simulation
                  ? "Сохранить симуляцию"
                  : "Завершить активность"}
            <CheckCircle2 size={17} />
          </button>
        )}
      </div>
    </Dialog>
  );
}
