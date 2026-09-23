import { ArrowRight, ShieldCheck, Users } from "lucide-react";
import type { CareerClient } from "../api/client";
import "./public-access.css";

export type PublicConfig = Awaited<ReturnType<CareerClient["publicConfig"]>>;
export type PublicRole = "employee" | "hr";

type EntryProps = {
  config: PublicConfig;
  busy: boolean;
  onEnter: (role: PublicRole) => Promise<void>;
};

export function PublicAccess({ config, busy, onEnter }: EntryProps) {
  return (
    <section
      className="public-access"
      aria-labelledby="public-access-title"
      aria-busy={busy}
    >
      <p className="eyebrow">ОТКРЫТО ДЛЯ ЗНАКОМСТВА</p>
      <h3 id="public-access-title">Пройдите путь сами</h3>
      <p>
        Вымышленные сотрудники, настоящая работа платформы. Сервер создаёт
        отдельную копию данных для вашего браузера. Изменения не затронут других
        посетителей.
      </p>
      <div className="public-access-actions">
        <button
          className="button button-primary button-full"
          disabled={busy}
          onClick={() => void onEnter("employee").catch(() => undefined)}
        >
          <span>Кабинет сотрудника</span>
          <ArrowRight size={17} aria-hidden="true" />
        </button>
        <button
          className="button button-secondary button-full"
          disabled={busy}
          onClick={() => void onEnter("hr").catch(() => undefined)}
        >
          <Users size={17} aria-hidden="true" />
          <span>Кабинет HR</span>
        </button>
      </div>
      {busy && (
        <p className="public-access-status" role="status">
          Открываем вашу учебную копию…
        </p>
      )}
      <p className="public-access-detail">
        Сессия временная: до {Math.ceil(config.session_ttl_seconds / 60)} мин. В
        обоих кабинетах доступна одна и та же копия. Используйте только
        вымышленные данные.
      </p>
      {!config.ai_enabled && (
        <p className="public-access-ai-note">
          AI сейчас отключён. Можно проверить профиль, каталог, завершение
          активности, расчёт прогресса и HR-аналитику.
        </p>
      )}
    </section>
  );
}

export function PublicSessionNotice({
  config,
  role,
  busy,
  onEnter,
}: EntryProps & { role: PublicRole }) {
  return (
    <>
      <aside
        className="public-session-notice"
        aria-label="Публичная учебная сессия"
      >
        <div>
          <ShieldCheck size={17} aria-hidden="true" />
          <p>
            <strong>Ваша учебная копия</strong>
            <span>
              Вымышленные данные · настоящий сервер · временное хранение.
              {!config.ai_enabled ? " AI отключён." : ""}
            </span>
          </p>
        </div>
        <button
          disabled={busy}
          onClick={() =>
            void onEnter(role === "hr" ? "employee" : "hr").catch(
              () => undefined,
            )
          }
        >
          {role === "hr"
            ? "Перейти в кабинет сотрудника"
            : "Перейти в кабинет HR"}
          <ArrowRight size={15} aria-hidden="true" />
        </button>
      </aside>
      {role === "hr" && (
        <aside
          className="public-import-examples"
          aria-label="Данные для проверки импорта"
        >
          <p>
            Проверьте импорт: скачайте оба файла с вымышленными данными,
            выберите их в <a href="#/import">«Импорт данных»</a>, проверьте и
            подтвердите загрузку.
          </p>
          <div>
            <a
              href="/api/public/examples/employees.json"
              download="employees.json"
            >
              Пример профилей JSON
            </a>
            <a
              href="/api/public/examples/activity_history.csv"
              download="activity_history.csv"
            >
              Пример истории CSV
            </a>
          </div>
        </aside>
      )}
    </>
  );
}
