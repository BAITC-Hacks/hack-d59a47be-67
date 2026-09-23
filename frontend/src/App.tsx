import {
  Component,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ErrorInfo,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleHelp,
  Compass,
  Eye,
  EyeOff,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  RotateCcw,
  ShieldCheck,
  Target,
  Upload,
  Users,
  X,
} from "lucide-react";
import {
  ApiError,
  clearSession,
  createApiClient,
  getCsrf,
  type CareerClient,
} from "./api/client";
import { createDemoClient, resetDemo } from "./api/demo";
import { Ornament, QuestMark } from "./components/Ornament";
import {
  PublicAccess,
  PublicSessionNotice,
  type PublicConfig,
  type PublicRole,
} from "./components/PublicAccess";
import type { CatalogResponse, UserIdentity } from "./api/types";
import { AppContext } from "./context";
import { Dialog, ErrorAlert, Loading, Logo } from "./components/ui";
import EmployeeWorkspace, {
  type EmployeeView,
} from "./features/employee/EmployeeWorkspace";
import { HrPage } from "./features/hr/HrPage";
import { ImportPage } from "./features/import/ImportPage";

type Connection = { client: CareerClient; mode: "live" | "demo" };
const safeStorage = {
  get: (key: string) => {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set: (key: string, value: string) => {
    try {
      sessionStorage.setItem(key, value);
    } catch {
      /* memory session remains usable */
    }
  },
};
function initialConnection(): Connection {
  if (safeStorage.get("cq-ui-mode") === "demo")
    return {
      client: createDemoClient(
        safeStorage.get("cq-demo-role") === "hr" ? "hr" : "employee",
      ),
      mode: "demo",
    };
  return { client: createApiClient(), mode: "live" };
}
const navigation = [
  { id: "overview", label: "Моё развитие", icon: LayoutDashboard },
  { id: "skills", label: "Навыки и цель", icon: Target },
  { id: "recommendations", label: "Следующий шаг", icon: Compass },
  { id: "catalog", label: "Каталог активностей", icon: BookOpen },
  { id: "history", label: "История развития", icon: History },
] as const;

export default function App() {
  return (
    <AppErrorBoundary>
      <CareerApp />
    </AppErrorBoundary>
  );
}

function CareerApp() {
  const [connection, setConnection] = useState(initialConnection);
  const [user, setUser] = useState<UserIdentity | null>(null);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [loginError, setLoginError] = useState("");
  const [globalError, setGlobalError] = useState("");
  const [route, setRoute] = useState(
    () => window.location.hash.slice(2) || "overview",
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null);
  const [publicConfigError, setPublicConfigError] = useState(false);
  const [configRefresh, setConfigRefresh] = useState(0);
  const { client, mode } = connection;
  const connectionRef = useRef(connection);
  const authBusyRef = useRef(false);
  const authSequenceRef = useRef(0);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(
    () => () => {
      authSequenceRef.current++;
    },
    [],
  );
  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);
  useEffect(() => {
    connectionRef.current = connection;
  }, [connection]);
  useEffect(() => {
    let active = true;
    setPublicConfig(null);
    setPublicConfigError(false);
    if (mode !== "live") return;
    client
      .publicConfig()
      .then((config) => {
        if (active) setPublicConfig(config);
      })
      .catch(() => {
        if (active) setPublicConfigError(true);
      });
    return () => {
      active = false;
    };
  }, [client, mode, configRefresh]);
  const navigate = useCallback((path: string) => {
    window.location.hash = `/${path}`;
    setMenuOpen(false);
  }, []);
  useEffect(() => {
    const change = () => {
      setRoute(window.location.hash.slice(2) || "overview");
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  const handleError = useCallback((error: unknown) => {
    const message =
      error instanceof Error
        ? error.message
        : "Не удалось выполнить действие. Попробуйте ещё раз.";
    if (
      error instanceof ApiError &&
      (error.status === 401 || error.code === "CSRF_FAILED")
    ) {
      clearSession();
      setUser(null);
      setCatalog(null);
      setLoginError(message);
    }
    return message;
  }, []);
  useEffect(() => {
    let active = true;
    setRestoring(true);
    setUser(null);
    setCatalog(null);
    setGlobalError("");
    if (mode === "live" && !getCsrf()) {
      setRestoring(false);
      return;
    }
    client
      .me()
      .then((identity) => {
        if (active) setUser(identity);
      })
      .catch((e) => {
        if (active) setLoginError(handleError(e));
      })
      .finally(() => {
        if (active) setRestoring(false);
      });
    return () => {
      active = false;
    };
  }, [client, mode, handleError]);
  useEffect(() => {
    if (!user) return;
    let active = true;
    client
      .catalog()
      .then((value) => {
        if (active) setCatalog(value);
      })
      .catch((e) => {
        if (active) setGlobalError(handleError(e));
      });
    return () => {
      active = false;
    };
  }, [client, user, handleError]);
  const context = useMemo(
    () => (user ? { client, mode, user, catalog, handleError } : null),
    [client, mode, user, catalog, handleError],
  );

  async function signIn(
    action: () => ReturnType<CareerClient["login"]>,
    publicEntry = false,
  ) {
    if (authBusyRef.current) return;
    authBusyRef.current = true;
    const sequence = ++authSequenceRef.current;
    setSigningIn(true);
    setLoginError("");
    setGlobalError("");
    if (publicEntry) {
      // Role switching revokes the preceding server session. Hide its controls immediately.
      setUser(null);
      setCatalog(null);
    }
    try {
      const session = await action();
      if (
        connectionRef.current !== connection ||
        authSequenceRef.current !== sequence
      )
        return;
      setUser(session.user);
      safeStorage.set("cq-ui-mode", "live");
      navigate(session.user.role === "hr" ? "hr" : "overview");
    } catch (e) {
      if (
        connectionRef.current === connection &&
        authSequenceRef.current === sequence
      )
        setLoginError(handleError(e));
      throw e;
    } finally {
      if (
        connectionRef.current === connection &&
        authSequenceRef.current === sequence
      ) {
        authBusyRef.current = false;
        setSigningIn(false);
      }
    }
  }
  function login(username: string, password: string) {
    return signIn(() => client.login(username, password));
  }
  function enterPublic(role: PublicRole) {
    return signIn(() => client.publicSession(role), true);
  }
  function selectConnection(next: Connection) {
    authSequenceRef.current++;
    authBusyRef.current = false;
    setSigningIn(false);
    connectionRef.current = next;
    setConnection(next);
  }
  function enterDemo(role: "employee" | "hr" = "employee") {
    safeStorage.set("cq-ui-mode", "demo");
    safeStorage.set("cq-demo-role", role);
    setLoginError("");
    selectConnection({ client: createDemoClient(role), mode: "demo" });
    navigate(role === "hr" ? "hr" : "overview");
  }
  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setGlobalError("");
    try {
      await client.logout();
      if (connectionRef.current !== connection) return;
      clearSession();
      safeStorage.set("cq-ui-mode", "live");
      selectConnection({ client: createApiClient(), mode: "live" });
      setUser(null);
      setCatalog(null);
      navigate("overview");
    } catch (e) {
      if (connectionRef.current === connection) setGlobalError(handleError(e));
    } finally {
      setLoggingOut(false);
    }
  }
  if (restoring)
    return (
      <div className="boot-screen">
        <Logo />
        <Loading text="Открываем Career Quest…" />
      </div>
    );
  if (!user || !context)
    return (
      <LoginPage
        onLogin={login}
        onDemo={() => enterDemo()}
        onPublicLogin={enterPublic}
        publicConfig={publicConfig}
        publicConfigError={publicConfigError}
        onRetryConfig={() => setConfigRefresh((value) => value + 1)}
        busy={signingIn}
        error={loginError}
      />
    );
  const isHr = user.role === "hr";
  const employeePath = route.startsWith("employee/");
  let selectedEmployeeId = "";
  const employeeRouteParts = route.slice(9).split("/");
  if (employeePath) {
    try {
      selectedEmployeeId = decodeURIComponent(employeeRouteParts[0]);
    } catch {
      /* invalid route is handled below */
    }
  }
  const employeeView: EmployeeView = navigation.some(
    (item) => item.id === employeeRouteParts[1],
  )
    ? (employeeRouteParts[1] as EmployeeView)
    : "overview";
  const allowedView: EmployeeView = navigation.some((item) => item.id === route)
    ? (route as EmployeeView)
    : "overview";
  const actualPage = isHr
    ? route === "import"
      ? "import"
      : employeePath && selectedEmployeeId
        ? "employee"
        : "hr"
    : allowedView;
  const activeLabel = isHr
    ? actualPage === "import"
      ? "Импорт данных"
      : actualPage === "employee"
        ? "Профиль сотрудника"
        : "Обзор команды"
    : navigation.find((n) => n.id === allowedView)?.label;

  return (
    <AppContext.Provider value={context}>
      <div className="app-shell">
        <a
          className="skip-link"
          href="#main-content"
          onClick={(e) => {
            e.preventDefault();
            document.getElementById("main-content")?.focus();
          }}
        >
          Перейти к содержимому
        </a>
        {menuOpen && (
          <button
            className="sidebar-overlay"
            onClick={() => setMenuOpen(false)}
            aria-label="Закрыть меню"
          />
        )}
        <aside
          id="primary-sidebar"
          className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}
        >
          <div className="sidebar-brand">
            <Logo />
            <button
              className="icon-button mobile-only"
              aria-label="Закрыть меню"
              onClick={() => setMenuOpen(false)}
            >
              <X size={20} />
            </button>
          </div>
          <div className="workspace-label">
            <span className="workspace-mark">CQ</span>
            <div>
              <strong>Halyk · Career Quest</strong>
              <span>{isHr ? "Кабинет HR" : "Личный кабинет"}</span>
            </div>
          </div>
          <p className="nav-caption">{isHr ? "КОМАНДА" : "МОЙ ПУТЬ"}</p>
          <nav aria-label="Основная навигация">
            {isHr ? (
              <>
                <a
                  href="#/hr"
                  className={`nav-item ${actualPage !== "import" ? "active" : ""}`}
                  aria-current={actualPage !== "import" ? "page" : undefined}
                  onClick={() => setMenuOpen(false)}
                >
                  <Users size={19} />
                  <span>Обзор команды</span>
                </a>
                <a
                  href="#/import"
                  className={`nav-item ${actualPage === "import" ? "active" : ""}`}
                  aria-current={actualPage === "import" ? "page" : undefined}
                  onClick={() => setMenuOpen(false)}
                >
                  <Upload size={19} />
                  <span>Импорт данных</span>
                </a>
              </>
            ) : (
              navigation.map((item) => (
                <a
                  key={item.id}
                  href={`#/${item.id}`}
                  className={`nav-item ${allowedView === item.id ? "active" : ""}`}
                  aria-current={allowedView === item.id ? "page" : undefined}
                  onClick={() => setMenuOpen(false)}
                >
                  <item.icon size={19} />
                  <span>{item.label}</span>
                </a>
              ))
            )}
          </nav>
          <div className="sidebar-bottom">
            <div className="sidebar-tip">
              <Ornament />
              <strong lang="kk">Өсу өзіңнен басталады</strong>
              <p>Рост начинается с тебя.</p>
            </div>
            <button className="nav-item" onClick={() => setHelpOpen(true)}>
              <CircleHelp size={19} />
              Как это работает
            </button>
            {mode === "demo" && (
              <button
                className="nav-item"
                onClick={() => {
                  resetDemo();
                  enterDemo(isHr ? "hr" : "employee");
                }}
              >
                <RotateCcw size={17} />
                Начать демо заново
              </button>
            )}
            <div className="sidebar-user">
              <span className="avatar">{isHr ? "HR" : "Я"}</span>
              <div>
                <strong>{isHr ? "HR-специалист" : "Сотрудник"}</strong>
                <span>
                  {mode === "demo" ? "Учебный пример" : user.username}
                </span>
              </div>
              <button
                className="icon-button"
                title="Выйти"
                aria-label="Выйти"
                disabled={loggingOut}
                onClick={() => void logout()}
              >
                <LogOut size={18} />
              </button>
            </div>
          </div>
        </aside>
        <div className="main-shell">
          <header className="topbar">
            <div className="breadcrumb">
              <button
                className="icon-button mobile-only"
                aria-label="Открыть меню"
                ref={menuButtonRef}
                aria-expanded={menuOpen}
                aria-controls="primary-sidebar"
                onClick={() => setMenuOpen(true)}
              >
                <Menu size={22} />
              </button>
              <span>КАРЬЕРА И РАЗВИТИЕ</span>
              <ChevronRight size={14} />
              <strong>{activeLabel}</strong>
            </div>
            <Ornament className="topbar-ornament" />
            <div className="topbar-right">
              <span className="private-label">
                <ShieldCheck size={15} />
                {isHr ? "Доступ HR" : "Личный кабинет"}
              </span>
              <span className="topbar-avatar">{isHr ? "HR" : "Я"}</span>
            </div>
          </header>
          {mode === "live" && publicConfig?.enabled && (
            <PublicSessionNotice
              config={publicConfig}
              role={user.role}
              busy={signingIn || loggingOut}
              onEnter={enterPublic}
            />
          )}
          {mode === "demo" && (
            <div className="demo-banner">
              <span>
                <BookOpen size={15} />
                <strong>Демонстрация интерфейса</strong>
                <span>
                  Вымышленные данные и готовые примеры. Реальный AI не
                  вызывается.
                </span>
              </span>
              <button onClick={() => enterDemo(isHr ? "employee" : "hr")}>
                {isHr ? "Вид сотрудника" : "Посмотреть HR"}
                <ArrowRight size={15} />
              </button>
            </div>
          )}
          <main id="main-content" className="main-content" tabIndex={-1}>
            {globalError && <ErrorAlert message={globalError} />}
            {isHr ? (
              actualPage === "import" ? (
                <ImportPage
                  onDone={() => navigate("hr")}
                  publicDemo={publicConfig?.enabled === true && mode === "live"}
                />
              ) : actualPage === "employee" ? (
                <>
                  <button
                    className="button button-ghost back-button"
                    onClick={() => navigate("hr")}
                  >
                    ← К обзору команды
                  </button>
                  <EmployeeWorkspace
                    key={selectedEmployeeId}
                    employeeId={selectedEmployeeId}
                    view={employeeView}
                    navigate={(view) =>
                      navigate(
                        `employee/${encodeURIComponent(selectedEmployeeId)}/${view}`,
                      )
                    }
                    isHr
                  />
                </>
              ) : (
                <HrPage
                  onOpenEmployee={(id) =>
                    navigate(`employee/${encodeURIComponent(id)}`)
                  }
                  onOpenImport={() => navigate("import")}
                />
              )
            ) : (
              <EmployeeWorkspace
                key={user.employee_id!}
                employeeId={user.employee_id!}
                view={allowedView}
                navigate={navigate}
              />
            )}
          </main>
          <footer className="app-footer">
            <span>
              Career Quest <span className="muted">·</span> Прототип для кейса
              Halyk Bank
            </span>
            <span>
              <span lang="kk">Болашаққа бірге</span>
            </span>
          </footer>
        </div>
        {helpOpen && (
          <Dialog
            title="Ваш путь развития — по шагам"
            onClose={() => setHelpOpen(false)}
          >
            <ol className="help-steps">
              <li>
                <strong>Выберите карьерную цель</strong>
                <p>Сравните текущие навыки с требованиями желаемой роли.</p>
              </li>
              <li>
                <strong>Найдите подходящую активность</strong>
                <p>
                  Рекомендация объяснит, как шаг связан с вашей целью и
                  историей.
                </p>
              </li>
              <li>
                <strong>Завершите и посмотрите результат</strong>
                <p>
                  После подтверждения увидите изменения навыков и прогресса.
                </p>
              </li>
            </ol>
            <div className="alert">
              <ShieldCheck size={20} />
              <span>
                Сотрудник видит свой профиль. HR получает доступ для поддержки
                развития и загрузки данных. Публичного рейтинга нет.
              </span>
            </div>
            <button
              className="button button-primary button-full"
              onClick={() => setHelpOpen(false)}
            >
              Всё понятно
              <Check size={17} />
            </button>
          </Dialog>
        )}
      </div>
    </AppContext.Provider>
  );
}

function LoginPage({
  onLogin,
  onDemo,
  onPublicLogin,
  publicConfig,
  publicConfigError,
  onRetryConfig,
  busy,
  error,
}: {
  onLogin: (username: string, password: string) => Promise<void>;
  onDemo: () => void;
  onPublicLogin: (role: PublicRole) => Promise<void>;
  publicConfig: PublicConfig | null;
  publicConfigError: boolean;
  onRetryConfig: () => void;
  busy: boolean;
  error: string;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    try {
      await onLogin(username.trim(), password);
    } catch {
      /* Parent displays the localized message. */
    }
  }
  return (
    <div className="login-layout">
      <section className="login-story">
        <Logo />
        <div className="login-story-content">
          <span className="story-eyebrow">HALYK · CAREER QUEST</span>
          <h1 lang="kk">
            Болашаққа
            <br />
            <span>бірге.</span>
          </h1>
          <p className="story-translation">К следующей ступени — вместе.</p>
          <p>
            Профессиональный рост начинается с понятного плана. Узнайте, какие
            навыки развивать и как каждая активность приближает вас к цели.
          </p>
          <div className="story-stages">
            <span>
              <b>01</b>Выберите цель
            </span>
            <span>
              <b>02</b>Развивайте навыки
            </span>
            <span>
              <b>03</b>Следите за прогрессом
            </span>
          </div>
        </div>
        <Ornament className="login-ornament" />
        <span className="story-footer">Мансап жолы · Ваш карьерный путь</span>
      </section>
      <section className="login-panel">
        <span className="login-panel-kicker">CAREER QUEST / ВХОД</span>
        <div className="login-form-wrap">
          <span className="login-compass">
            <QuestMark />
          </span>
          <h2>
            {publicConfig?.enabled ? "Добро пожаловать" : "Вход в кабинет"}
          </h2>
          <p className="login-description">
            Ваши навыки, карьерная цель и история развития.
          </p>
          {error && <ErrorAlert message={error} />}
          {publicConfig?.enabled ? (
            <PublicAccess
              config={publicConfig}
              busy={busy}
              onEnter={onPublicLogin}
            />
          ) : (
            <>
              <form onSubmit={(e) => void submit(e)}>
                <label className="field-label" htmlFor="username">
                  Логин
                </label>
                <input
                  className="field-input"
                  id="username"
                  name="username"
                  autoComplete="username"
                  placeholder="Ваш рабочий логин"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  disabled={busy}
                />
                <label className="field-label" htmlFor="password">
                  Пароль
                </label>
                <div className="password-field">
                  <input
                    className="field-input"
                    id="password"
                    name="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    placeholder="Введите пароль"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    disabled={busy}
                  />
                  <button
                    className="icon-button"
                    type="button"
                    aria-label={
                      showPassword ? "Скрыть пароль" : "Показать пароль"
                    }
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
                <button
                  className="button button-primary button-full login-submit"
                  disabled={busy}
                >
                  {busy ? "Входим…" : "Войти в кабинет"}
                  <ArrowRight size={19} />
                </button>
              </form>
              <p className="account-help">
                Учётную запись предоставляет HR вашей команды.
              </p>
              <div className="login-divider">
                <span>Познакомиться с продуктом</span>
              </div>
              <button
                className="button button-secondary button-full demo-login"
                onClick={onDemo}
                disabled={busy}
              >
                <BookOpen size={18} />
                Посмотреть демо
                <ArrowUpRight size={17} />
              </button>
              <p className="demo-login-note">
                Готовые примеры интерфейса. Без реального подбора рекомендаций.
              </p>
            </>
          )}
          {publicConfigError && (
            <div className="public-config-retry" role="status">
              <p>
                Не удалось проверить быстрый доступ к серверу. Обычный вход
                доступен по учётной записи.
              </p>
              <button
                className="button button-secondary"
                onClick={onRetryConfig}
                disabled={busy}
              >
                Проверить быстрый доступ
              </button>
            </div>
          )}
        </div>
        <span className="login-security">
          <ShieldCheck size={16} />
          Личное развитие. Без публичных рейтингов.
        </span>
      </section>
    </div>
  );
}
class AppErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* Never log profiles or credentials. */
  }
  render() {
    return this.state.failed ? (
      <div className="boot-screen">
        <Logo />
        <h1>Не получилось показать страницу</h1>
        <p>
          Обновите страницу. Сохранённый на сервере прогресс останется на месте.
        </p>
        <button
          className="button button-primary"
          onClick={() => window.location.reload()}
        >
          Обновить страницу
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}
