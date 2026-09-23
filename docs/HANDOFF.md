# Career Quest: передача backend-каркаса

Алихан отвечает за backend, данные, API, права, интеграцию и деплой. Олег — за `backend/app/ai/` и собственные тесты. Батыр — за `frontend/`. Этот каркас не меняет каталоги Олега и Батыра.

## Где контракт и что реализовано

- Единственные Python-схемы: [`backend/app/contracts.py`](../backend/app/contracts.py). Не копировать модели в AI-модуль и не вводить несовместимые поля в frontend.
- HTTP v1: [`contracts/API.md`](../contracts/API.md); AI: [`contracts/AI_CONTRACT.md`](../contracts/AI_CONTRACT.md).
- Экспорт приложения: [`contracts/openapi.json`](../contracts/openapi.json), обновление командой `scripts/export-openapi`.
- В [`contracts/examples/`](../contracts/examples/) лежат отдельные синтетические fixtures команды, проверяемые `backend/tests/test_contracts.py`. Это примеры payloads, а не ответы работающей предметной логики и не копии стартового датасета.

Реализованы `GET /api/health`, `GET /api/health/ready`, `GET /api/version`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me`.

Предметные маршруты зарегистрированы, проверяют доступ и входные модели, затем возвращают **501 `NOT_IMPLEMENTED`**. Это catalog, список/профиль сотрудников, изменение цели, рекомендации, preview, completions, HR summary и import. В OpenAPI у них tag `planned` и `x-implementation-status: planned`; для готовых маршрутов — `implemented`. Правильно переданная форма запроса не означает, что изменение выполнено. Для чужого сотрудника сначала 403; HR/владельцу при отсутствующем employee — 404; без сессии — 401.

Текущие capabilities:

```json
{
  "ai": {"status": "not_configured", "engine": "none"},
  "dataset": {"status": "not_loaded", "version": null}
}
```

Dataset пока не загружен. Наличие исходников на диске и созданной demo-учётной записи не меняет этот статус. Liveness проверяет только процесс. Readiness проверяет БД и миграции: при успехе 200 `ReadinessResponse`; при сбое 503 `ErrorResponse` с code `NOT_READY`, безопасными details.database/capabilities. Отсутствие AI или датасета не маскируется и само по себе не делает готовую БД недоступной. `/api/version` показывает API `1.0.0` и commit SHA либо `unknown`.

## Для Батыра: cookie, Origin и CSRF

Учётные записи и связи employee/HR создаются серверным оператором; браузер передаёт только username/password. Публичной регистрации и банковского SSO нет. Employee видит только собственный employee_id; HR имеет доступ к HR-маршрутам и профилям сотрудников.

Cookie называется **`cq_session`**: `HttpOnly`, `SameSite=Lax`, `Path=/api`, без Domain. TTL по умолчанию **3600 секунд** (`SESSION_TTL_SECONDS`). В HTTPS-staging `Secure` включён обязательно. Logout удаляет запись сессии и cookie. Пароли хешируются PBKDF2; в БД хранятся hash сессионного токена и hash CSRF.

Login требует точный разрешённый **Origin**, но ещё не требует CSRF. Все последующие POST/PATCH требуют одновременно сессию, разрешённый Origin и **`X-CSRF-Token`**. GET не требует CSRF. Браузер устанавливает Origin автоматически; не пытайтесь подменять его JavaScript-заголовком. У небраузерного клиента Origin необходимо передать явно. Login ограничен по username и IP; 429 содержит `Retry-After: 900`.

Raw CSRF возвращается **только при login** в `SessionResponse.csrf_token`. `/api/me` возвращает `UserIdentity` (`id,username,role,employee_id`) и не восстанавливает raw CSRF. Для восстановления после reload сохраняйте CSRF в `sessionStorage` и очищайте его при logout/401; если токен потерян, нужен повторный login. Сессионную cookie не переносить в JavaScript-хранилище; её читает только браузер. При новой авторизации используйте новый csrf_token.

Для локальной интеграции используйте одинаковый hostname: например, frontend `http://localhost:5173` и API `http://localhost:8000`. При frontend на `127.0.0.1:5173` добавьте этот точный Origin в `ALLOWED_ORIGINS` и обращайтесь к API через `127.0.0.1:8000`. Смешивание `localhost` и `127.0.0.1` делает запрос cross-site, и SameSite=Lax мешает отправке cookie. В staging используйте HTTPS и same-site frontend/API, предпочтительно один origin через reverse proxy. Включённый CORS сам по себе не отменяет правила SameSite.

Пример для браузерного frontend на `http://localhost:5173` (пароль приходит из формы, не из исходного кода):

```javascript
const API = "http://localhost:8000";

async function login(username, password) {
  const response = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username, password}),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(`${body.code}: ${body.message}`);
  sessionStorage.setItem("cq_csrf", body.csrf_token);
  return body.user;
}

async function currentUser() {
  const response = await fetch(`${API}/api/me`, {credentials: "include"});
  const body = await response.json();
  if (response.status === 401) sessionStorage.removeItem("cq_csrf");
  if (!response.ok) throw new Error(`${body.code}: ${body.message}`);
  return body;
}

async function logout() {
  const response = await fetch(`${API}/api/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: {"X-CSRF-Token": sessionStorage.getItem("cq_csrf") || ""},
  });
  const body = await response.json();
  if (response.ok || response.status === 401) sessionStorage.removeItem("cq_csrf");
  if (!response.ok) throw new Error(`${body.code}: ${body.message}`);
  return body; // {status: "logged_out"}
}
```

Пример будущего запроса рекомендаций для employee_id из собственной `UserIdentity`:

```javascript
const response = await fetch(
  `${API}/api/employees/${encodeURIComponent(user.employee_id)}/recommendations`,
  {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": sessionStorage.getItem("cq_csrf") || "",
    },
    body: JSON.stringify({scenario_date: "2026-10-01", limit: 3}),
  },
);
const body = await response.json();
if (response.status === 501) {
  // Покажите «Функция ещё не подключена»; не пустой список рекомендаций.
} else if (!response.ok) {
  // Обработайте code/message; request_id нужен для безопасной диагностики.
}
```

Все ошибки имеют `{code,message,request_id,details}`. 401 — вход/сессия, 403 — права/Origin/CSRF, 404 — объект/маршрут, 409 — конфликт состояния, 422 — схема, 429 — login rate limit, 501 — planned, 503 — временная недоступность. `details` не содержит исходные значения паролей, импортов или профилей. Данные 409 и будущие успешные предметные ответы пока определены контрактом; это не реализованная логика.

## Для Олега: вход и выход

Backend-adapter находится в [`backend/app/ai_adapter.py`](../backend/app/ai_adapter.py), вне AI-каталога. При `AI_ENABLED=true` он пробует импортировать **`backend.app.ai`** и взять экспортированную **async** функцию `recommend`. Поэтому пакет Олега должен предоставить эту функцию из своего `__init__.py` либо другого эквивалентного entry point `backend.app.ai`. Адаптер вызывает её внутри того же процесса; отдельного HTTP AI-сервера и LLM-клиента этот каркас не создаёт.

```python
from backend.app.contracts import RecommendationContext, RecommendationResult

async def recommend(context: RecommendationContext) -> RecommendationResult:
    ...  # Реализация принадлежит Олегу.
```

`AI_ENABLED=false` по умолчанию. Отсутствующий/сломанный optional module не мешает запуску API: capability остаётся `not_configured/none`. При подключённой async-функции — `configured/oleg`. На вызов выделено 10 секунд; исключение, timeout, неверный engine, неизвестные event_id/evidence_ids, превышение лимита или `no_candidates` при наличии кандидатов приводят к `unavailable/oleg` с пустым списком. Адаптер передаёт глубокую копию context и не записывает данные.

Context содержит `contract_version`, `data_version`, `state_version`, `scenario_date`, обезличенный `profile`, `goal`, `current_skills`, `gaps`, `eligible_candidates`, `facts` и `limit`. Профиль имеет только `profile_ref,role,grade`, без кадрового employee_id и ФИО. Кандидаты уже допустимы, их эффекты рассчитаны backend. Факты имеют `evidence_id`. Уровни навыков — 0–5; цель — `{target_role,target_grade}`; дата сценария примеров — 2026-10-01. Не добавляйте собственную шкалу и не подменяйте дату сценария системным временем.

Result:

```json
{
  "status": "ok",
  "engine": "oleg",
  "recommendations": [
    {
      "event_id": "SYNTHETIC_EVENT_001",
      "explanation": {
        "text": "Синтетический пример: выбранное занятие сокращает разрыв навыка.",
        "evidence_ids": ["synthetic-gap-001", "synthetic-event-001"]
      }
    }
  ]
}
```

| status / engine | Значение |
| --- | --- |
| `ok / oleg` | 1–3 выбранных кандидата, не больше context.limit |
| `no_candidates / oleg` | Допустимых кандидатов нет; recommendations=[] |
| `not_configured / none` | Движок не подключён; recommendations=[] |
| `unavailable / oleg` | Подключённый движок не дал допустимого результата; recommendations=[] |

Эти статусы нельзя путать с текущим HTTP 501. Пустой список при `not_configured` не означает отсутствие подходящих активностей. AI выбирает только event_id из context и объясняет выбор через существующие evidence_ids. Backend выполняет арифметику, фильтрацию доступности, проверку прав, транзакционную запись и обогащает внешний HTTP-ответ названиями/эффектами. Данные не отправляются внешнему сервису без разрешения.

## Локальные команды и ограничения

Из корня репозитория, с подготовленным `.venv`:

```sh
.venv/bin/python -m backend.app.cli migrate
.venv/bin/python -m backend.app.cli create-account --username demo.employee --role employee --employee-id SYNTH_EMPLOYEE_001 --synthetic
.venv/bin/python -m backend.app.cli create-account --username demo.hr --role hr
scripts/dev
```

CLI запрашивает пароль интерактивно и повторно; пароли не задаются аргументом команды и не имеют значений по умолчанию. `--synthetic` создаёт только явно синтетический employee с префиксом `SYNTH_`. Не используйте пароли из fixtures как реальные credentials. Другая команда/терминал:

```sh
scripts/test -q
scripts/export-openapi
```

Приложение использует одну SQLite-БД, один backend-процесс и одну реплику. При запуске применяются версионированные миграции; существующие записи сохраняются. WAL выключен по умолчанию; включение требует локального диска одного хоста. Docker Compose и `.env.example` описаны в README. Локальные URL в этом документе — адреса настройки; фактические проверки запуска и ограничения окружения фиксируются в итоговом отчёте.

Следующий этап — реализовать проверяемый импорт исходных JSON/CSV, доменные расчёты и предметные endpoints в рамках единого контракта; подключить код Олега через adapter; подключить frontend Батыра. Preview остаётся дополнительной функцией команды без записи. Каркас не заявляет готовое AI-ядро, совместимый работающий импорт или публичный деплой.
