# Передача backend Career Quest

Алихан: backend/данные/API/права/интеграция/деплой. Олег: `backend/app/ai/` и собственные тесты. Батыр: `frontend/`. Код AI и frontend не изменён. PR направляется из отдельной backend-ветки в `codex/architecture-foundation`, без самостоятельного merge.

## Запуск и проверки

Нужен Python 3.12; на проверенной машине использован локальный Python 3.12.14. Глобальные установки не нужны.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
cp .env.example .env
# Сначала validation; путь указывает на локальные 4 исходных файла, не ZIP:
.venv/bin/python -m backend.app.cli import-kit /path/to/career_quest_dataset
# Явный atomic commit после повторной validation:
.venv/bin/python -m backend.app.cli import-kit /path/to/career_quest_dataset --commit
.venv/bin/python -m backend.app.cli create-account --username demo-hr --role hr
.venv/bin/python -m backend.app.cli create-account --username demo-employee --role employee --employee-id EXISTING_EMPLOYEE_ID
scripts/dev
```

Пароли CLI вводятся скрыто, 12–256 символов, общих паролей в Git нет. `--synthetic` создаёт только пустую синтетическую identity, а для предметных экранов нужен импорт полного синтетического профиля/кита. Менеджер из данных автоматически не получает HR-права.

```sh
make check                # исходные 21 unit test + backend pytest + schemas + Ruff
scripts/smoke             # реальный TCP-сценарий, временная синтетическая БД, два запуска
scripts/export-openapi
.venv/bin/ruff format --check backend scripts/smoke.py scripts/check_contracts.py
.venv/bin/python scripts/check_agent_log.py --base origin/codex/architecture-foundation
```

Рабочий URL после `scripts/dev`: `http://127.0.0.1:8000/docs`. Один процесс, миграции без reset. Для контейнера: `COMMIT_SHA=$(git rev-parse HEAD) docker compose -f compose.yaml up --build`; постоянный `sqlite_data`, non-root, bind только `127.0.0.1:8000`. Docker на этой машине отсутствует, container build не проверен. GitHub Actions billing-blocked: локальные результаты не выдаются за зелёный CI.

## Повторяемая совместная проверка до merge PR

Обновите refs и запустите из backend-ветки с установленным Python 3.12 dev venv:

```sh
git fetch origin
scripts/verify-integration --backend-ref HEAD --ai-ref origin/codex/ai-recommendations
```

Команда проверяет именно **закоммиченные** refs, строит виртуальное Git-дерево и экспортирует его во временную папку. Рабочие правки, .env и локальная БД не копируются; PR и refs не сливаются/не переключаются. При конфликте проверка останавливается. В отчёте печатаются точные SHA backend/AI/tree. Нужны обе ветки в локальном Git; загрузка зависимостей не выполняется автоматически.

В изолированной сборке выполняются исходные тесты, backend/AI pytest, schemas/Ruff и `scripts/smoke --ai`. Последний запускает настоящий API/AI-модуль по TCP дважды, подменяя только remote selector в тестовом процессе: login → recommendations → completion → restart → idempotency replay/latest stale → logout. У процесса только временная синтетическая БД и фиктивная конфигурация; этот test entrypoint исключён из Docker image и не служит fallback приложения. Ключ и реальные данные для этой проверки не нужны.

На машине с Docker:

```sh
scripts/verify-integration --ai-ref origin/codex/ai-recommendations --container
# После объединения нужного кода также доступна самостоятельная проверка:
scripts/check-container --require-ai
```

Контейнерная команда использует отдельный случайный Compose project и временные синтетические данные. Она не устанавливает Docker, не читает рабочий .env и не очищает существующие контейнеры/volumes. Без Docker завершается понятной ошибкой; это не успешный container build. Результат реальной сборки на текущей машине пока отсутствует.

## Контракты

Единственный источник: `backend/app/contracts.py`. `contracts/API.md`, `contracts/AI_CONTRACT.md`, `contracts/openapi.json`; JSON Schema AI с прежними именами теперь генерируется из этих же моделей. Все `contracts/examples/*.synthetic.json` — независимые вымышленные примеры, не профили кита. Точечное расширение согласовано владельцем задачи: `docs/CONTRACT_CHANGE_PROPOSAL.md`.

Реализованы catalog, HR pagination, profile/history/goal/progress, preview, completion, HR counts, двухфазный import, recommendation generation boundary и latest. Предметные маршруты больше не возвращают planned 501. Без кита доменные запросы получают 503 `DATASET_NOT_LOADED`; инфраструктурная readiness при исправной БД остаётся 200 и отдельно показывает capabilities.

`state_version` / `expected_state_version` — глобальная revision. `data_version` HTTP-профиля содержит версию кита и revision. Новая цель/завершение/изменяющий импорт делают прежние рекомендации stale. No-op импорт, preview, auth и сохранение рекомендации revision не увеличивают.

## Батыр: cookie, CSRF и рабочий сценарий

Cookie `cq_session`: HttpOnly, SameSite=Lax, Path=/api, TTL по умолчанию 3600 s. В HTTPS staging Secure обязателен. Браузер: `credentials: "include"`, frontend `http://localhost:5173` → API `http://localhost:8000` (одинаковый hostname из-за SameSite). CORS с credentials использует только точные allowed origins. Login требует Origin; остальные POST/PATCH также `X-CSRF-Token` из login. JS не устанавливает Origin вручную; curl должен его передавать. `/api/me` возвращает только UserIdentity; CSRF храните в sessionStorage либо повторно входите после потери токена.

```js
const base = "http://localhost:8000";
const loginResponse = await fetch(`${base}/api/auth/login`, {
  method: "POST", credentials: "include",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({username, password}),
});
const session = await loginResponse.json(); // сначала проверьте loginResponse.ok
const headers = {"Content-Type": "application/json", "X-CSRF-Token": session.csrf_token};
const employeeId = session.user.employee_id; // сервер назначает role/employee_id
const profile = await fetch(`${base}/api/employees/${employeeId}`, {credentials: "include"}).then(r => r.json());
const recommendations = await fetch(`${base}/api/employees/${employeeId}/recommendations`, {
  method: "POST", credentials: "include", headers,
  body: JSON.stringify({scenario_date: profile.scenario_date, limit: 3}),
}).then(r => r.json());
// not_configured != no_candidates; не показывать отсутствующие рекомендации как готовое AI.
const preview = await fetch(`${base}/api/employees/${employeeId}/preview`, {
  method: "POST", credentials: "include", headers,
  body: JSON.stringify({expected_state_version: profile.state_version,
    scenario_date: profile.scenario_date, event_ids: [eventId]}),
});
// eventId — выбранное допустимое мероприятие каталога/рекомендации.
const body = JSON.stringify({expected_state_version: profile.state_version,
  event_id: eventId, mode: "demo_simulation"});
const idempotencyKey = crypto.randomUUID();
const complete = () => fetch(`${base}/api/employees/${employeeId}/completions`, {
  method: "POST", credentials: "include", headers: {...headers, "Idempotency-Key": idempotencyKey}, body,
});
const result = await complete();
// Если успешный ответ потерян, повторите complete() с ТЕМ ЖЕ body и key.
// Не подменяйте revision в этом повторе: сохранённый результат возвращается до revision-check.
const latest = await fetch(`${base}/api/employees/${employeeId}/recommendations/latest`, {credentials: "include"}).then(r => r.json());
// latest сохраняет полные карточки/объяснения; stale=true требует нового расчёта.
await fetch(`${base}/api/auth/logout`, {method: "POST", credentials: "include", headers});
```

Для конкретного существующего участия completion принимает `record_id`; mandatory допускается только как завершение существующего назначения. Повторные обязательные назначения сохраняются отдельными строками. Обычное завершение будущей сессии запрещено; явная demo_simulation отмечается в истории. EV_036 выбирает следующую незавершённую сессию. Preview не пишет данные.

Реализованный HTTP-сценарий проверен в `backend/tests/test_workflow.py` и TCP `scripts/smoke`: login → профиль → preview → complete → потерянный ответ/повтор → restart → точный сохранённый ответ → logout. Тестовые ответы AI — только fake в тестах, не runtime fallback.

Ошибки: `{code,message,request_id,details}`. 401 сессия; 403 чужой профиль/HR/Origin/CSRF; 404 объект; 409 revision/key/state; 422 схема; 429 login limit; 503 dataset/storage. Чужой доступ проверяется до idempotency-cache и выдачи данных.

## HR: совместный импорт жюри

`POST /api/hr/import`, только HR, cookie+Origin+CSRF:

```json
{"dry_run":true,"files":[
  {"source_filename":"employees.json","source_format":"json","content":"<исходный JSON {meta,employees}>"},
  {"source_filename":"activity_history.csv","source_format":"csv","content":"<исходный CSV с заголовком>"}
]}
```

Это иллюстрация transport; валидные JSON-примеры есть в contracts/examples. Получите `preview_token`, `revision`, `counts`; отправьте те же files с `dry_run=false, preview_token`. Token живёт 15 минут и связан с точным содержимым/текущей revision. Ошибка любой строки отменяет всю партию. Новые профили и их история валидируются совместно. `record_id` — ключ; разные ID не дедуплицируются по employee/event/date. Повтор исходного импорта после локального completion/изменения goal не стирает локальные действия: исходные source_record/source_json сохраняются отдельно.

Изменённые существующие employee_id/record_id конфликтуют; режим их исправления/replace ещё не реализован. skills.json и events.json заменяют соответствующий каталог целиком после preview/commit и проверки всех сохранённых ссылок. Preview предупреждает о пересчёте профилей по новому каталогу и истории; commit обновляет revision и делает рекомендации stale. HR summary сейчас содержит counts сотрудников/целей/завершений/симуляций; расширенная аналитика разрывов/участия из проектной записки остаётся следующим срезом.

## Олег: in-process boundary

Экспорт из `backend.app.ai`:

```python
from backend.app.contracts import RecommendationContext, RecommendationResult

async def recommend(context: RecommendationContext) -> RecommendationResult:
    ...  # Реализация Олега; backend её не создаёт.
```

`AI_ENABLED=false` по умолчанию. Missing/broken module не мешает запуску. Включённый async export → capability configured/oleg. Timeout 7 s внутри бюджета API 10 s; функция не должна блокировать event loop. Backend закрывает DB transaction ДО await, затем повторно сверяет revision; изменения во время вызова →409 без сохранения устаревшего ответа.

Context содержит обезличенный профиль, цель, версию и дату, вычисленные skills/gaps, допустимых полезных кандидатов с effects и facts/evidence_id. Выберите до трёх event_id; для каждого evidence_ids должны подтверждать минимум goal/grade, gap данного события и history (включая факт отсутствия истории). Backend проверяет ссылки и собирает HTTP-текст из доверенных facts: свободные утверждения модели не публикуются. Полные карточки сохраняются и возвращаются latest после перезапуска.

HTTP статусы: `ok/oleg`, `unavailable/oleg`, `not_configured/none`, `no_candidates/none`, `no_target/none`; stale — отдельный boolean. Python RecommendationResult не расширялся no_target: без цели/кандидатов backend не вызывает AI. Никакой LLM-интеграции, внешней отправки данных или отдельного AI-сервера эта ветка не добавляет.

## Подключение реализации Олега из PR #3

AI реализован в [PR #3](https://github.com/BAITC-Hacks/hack-d59a47be-67/pull/3); backend-дополнения не копируют и не меняют его код. Для совместного запуска нужны обе порции изменений после review. Одно наличие настроек в backend-ветке без модуля по-прежнему даёт `not_configured/none`.

Runtime `requirements.lock` теперь включает те же проверенные HTTP transport pins, что AI requirements Олега: httpx0.28.1, httpcore1.0.9, certifi2026.7.22. Docker использует этот единый runtime lock: дополнительный COPY отсутствующего AI requirements не нужен, Dockerfile не требует наличия optional AI при установке зависимостей; запуск без модуля сохраняет not_configured. `.dockerignore` разрешает только Python-файлы модуля и requirements.txt; AI fixtures/отчёты/README, данные и секреты в контекст не включаются.

Локальные настройки `.env` после подключения PR #3:

```dotenv
AI_ENABLED=true
OPENAI_API_KEY=<local secret supplied by the operator>
OPENAI_MODEL=gpt-4.1-mini-2025-04-14
AI_TIMEOUT_SECONDS=6.0
```

Compose передаёт только эти явно перечисленные переменные в runtime; ключ не используется как build arg и не копируется в образ. По умолчанию `AI_ENABLED=false`, ключ пуст. Не публикуйте вывод `docker compose config` с реальным ключом: подстановка environment может его показать. Наличие `configured/oleg` подтверждает загрузку async-модуля, а не доступность провайдера/ключа. AI timeout6 s вложен в backend7 s. Ошибка провайдера — `unavailable/oleg`; frontend отличает это от отсутствия кандидатов.

Backend дополнил facts критичными навыками именно выбранной цели и отдельными непересекающимися history-выборками для события и других событий того же type+format. Counts/даты/sample/provenance получены сервером, нулевые выборки не интерпретируются как предпочтения. Python- и HTTP-схемы неизменны; детали — `contracts/AI_CONTRACT.md`. Расширенные синтетические примеры — `contracts/examples/ai_enriched_context.synthetic.json` и `ai_enriched_result.synthetic.json`; прежний минимальный пример сохранён для потребителей и тестов Олега.

Обычные проверки не отправляют данные в OpenAI. Реальный ключ с другой рабочей машины не переносился. Live-результат Олега 3/3 относится к его синтетическим evaluation fixtures; качество на новом backend-контексте здесь ещё не измерялось. Docker по-прежнему отсутствует: allowlist/config/dependencies подготовлены, но container build/Compose runtime не заявлены проверенными.

## Время, навыки и ограничения

Дата demo из metadata: 2026-10-01. Исторический date — proxy завершения, `completed_at=null`, `date_source=historical_proxy`. Начисляется каждый completed строго после last_review_date и до среза включительно; значения baseline не меняются. Новые completed_at используют серверные demo-часы Asia/Almaty, реальные UTC-часы сохраняются отдельно в recorded_at. На одинаковую дату новые факты сортируются по completed_at, не случайному ID. Gain/cap/0–5 никогда не снижают навык. Цель не расширяет текущую аудиторию события. Lead без явной цели — no_target.

AI-ядро принадлежит Олегу и передано отдельным PR #3, frontend принадлежит Батыру; публичного деплоя нет. Проверенные результаты и ограничения среды — `VERIFICATION.md`. Исходные файлы/ZIP/жюри/БД/.env/секреты в Git и Docker image не входят.
