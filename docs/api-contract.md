# HTTP-контракт v1

Проект интерфейса для согласования до реализации. Сейчас HTTP-сервер отсутствует. Prefix `/api/v1`, JSON UTF-8, ISO даты, UTC timestamp с offset. Dataset clock передаётся как `as_of_date`. AI contract — отдельные машиночитаемые схемы в `contracts/`; полную OpenAPI генерирует FastAPI после реализации DTO, затем фиксирует в `contracts/openapi.json`.

## Общие правила

- Авторизация: серверная cookie session. `employee_id` в URL не даёт права читать чужой профиль. Employee scope всегда равен identity; HR scope проверяется явно.
- Все доменные GET возвращают `meta: {data_revision, as_of_date, policy_version}`. Отсутствующая сущность=404, чужая=403, неавторизован=401.
- Команды изменения доменных данных (создание/завершение participation, import commit) принимают `Idempotency-Key` и `expected_revision` в body. Порядок: auth и права → поиск сохранённого результата по actor+operation+key → проверка request hash → повтор того же тела возвращает сохранённый результат **до проверки revision**. Только новый ключ проверяет revision в транзакции; mismatch=409 с текущей revision. Иначе повтор после потери успешного ответа ошибочно получил бы 409. POST recommendations/validate не меняют навыки и не требуют Idempotency-Key, но проверяют expected_revision; login/logout не являются доменными изменениями.
- Общая ошибка: `{error: {code, message, details: [{path, code, message}], request_id}}`. Ответ не включает traceback, prompt и ключ провайдера.
- UI локализует server error code; имена/описания датасета не переводятся под видом исходного текста. RU — основной UI MVP; KZ/EN локализация опциональна.

| Метод / путь | Доступ | Request | Response / смысл |
|---|---|---|---|
| `GET /health` | public | — | `{status, schema_version}` без dataset records |
| `POST /auth/login` | public, rate limit | `{username,password}` | session cookie, `{user:{role,employee_id}}`; только заранее заведённые demo users |
| `POST /auth/logout` | authenticated | CSRF | 204, удалить серверную сессию |
| `GET /me` | authenticated | — | identity + разрешённые возможности |
| `GET /employees` | HR | cursor, limit<=100, role/grade optional | `{items:[{employee_id,full_name,role,grade}],next_cursor,meta}` |
| `GET /employees/{id}` | self/HR | — | profile, baseline/current skills, goal, trajectory, history summary, meta |
| `GET /employees/{id}/history` | self/HR | cursor, limit<=100 | нормализованные participations и provenance dates, meta |
| `GET /employees/{id}/events` | self/HR | — | доступные catalogue items, candidate flag, exclusion reason codes; отдельно continue items; meta |
| `POST /employees/{id}/recommendations` | self/HR | `{expected_revision,locale}` | `{result:RecommendationResult,explanations:[...],meta}` по описанному ниже envelope; `context_id` связывает evidence и revision |
| `GET /employees/{id}/recommendations/latest` | self/HR | — | `{state:not_generated\|fresh\|stale, recommendation:null\|RecommendationEnvelope, meta}`; не запускает LLM; envelope тот же, что POST |
| `POST /employees/{id}/participations` | self | `{expected_revision,event_id,session_date:null\|date}` | 201 `{participation,meta}`; только доступная voluntary activity; status=in_progress |
| `POST /participations/{id}/complete` | owner employee | `{expected_revision}` | `{participation,skill_changes:[{skill_id,before,after,gain}],trajectory_before,trajectory_after,meta}` |
| `POST /imports/validate` | HR | multipart employees.json + activity_history.csv, mode, expected_revision | staged preview ниже; никаких доменных записей |
| `POST /imports/{batch_id}/commit` | создавший HR | `{expected_revision}` | `{batch_id,inserted,updated,unchanged,data_revision}`; импорт всей партии |
| `GET /hr/overview` | HR | date_from,date_to,department optional | skill_gaps, no_step_counts, recommendation_state_counts, participation counts + meta |
| `GET /hr/employees-without-step` | HR | reason,cursor,limit<=100 | доступный только HR список с причинами; decision engine без вызова LLM |

Опциональное изменение career_goal не входит в первый vertical slice. Для Lead без цели UI пока сообщает no_target; если цель задаётся из UI, участник 2 добавляет `PATCH /employees/{id}/goal` с той же auth/revision/idempotency семантикой и обновляет контракт.

## Подготовленный AI-контекст и объяснения

Внутренний вызов `recommend(context)` не является отдельным публичным endpoint. Backend формирует facts и сохраняет context snapshot на время валидации. Модуль возвращает recommendations + evidence refs; HTTP-слой в ответе карточки разрешает refs в отображаемые факты из того же snapshot. Клиент никогда не получает неподтверждённый raw LLM output.

Внешний ответ: `{result: RecommendationResult, explanations:[{event_id,title,text,facts:[{evidence_id,category,text}]}],meta}`. `explanations` содержит одну запись на каждый выбранный event_id, `facts` разрешает все его evidence_ids в строки; пустой результат имеет `explanations=[]`. Внутренний RecommendationResult остаётся provider-neutral; HTTP envelope не изменяет JSON Schema AI-модуля. Текст фактов и чисел вычисляется на сервере.

Этот shape называется `RecommendationEnvelope`; validated result и resolved explanations сохраняются вместе в recommendation_runs, поэтому latest после reload возвращает полные карточки без повторного LLM. У stale envelope сохраняется его исходная revision, у внешнего latest.meta — текущая revision; UI помечает результат устаревшим и запрашивает новый. После идемпотентного повтора завершения UI перечитывает профиль: сохранённый ответ команды отражает состояние на момент её первого выполнения.

## Импорт жюри

Нативный профиль содержит **объект** `{meta:{dataset,version,as_of_date},employees:[...]}`, не bare array. CSV колонки: `record_id,employee_id,event_id,date,due_date,status,completion_pct,score,feedback_rating,assigned_by`. Профили и история могут загружаться вместе или по отдельности; FK проверяются против существующей БД + staged objects. Пустой отсутствующий файл не стирает существующие данные. Начальный полный kit и проверочные добавления используют один validator.

`mode=merge` по умолчанию: новые IDs добавляются, идентичные существующие игнорируются, изменённые существующие дают конфликт. `mode=replace_selected`: заменяются только явно переданные IDs; UI показывает diff и предлагает commit. Для изменённого профиля пересчитывается вся его история, не только новые строки. При повторном импорте одинакового набора revision не меняется, если ничего не изменилось.

Validation response:

```json
{
  "batch_id": "synthetic-batch-id",
  "base_revision": 1,
  "expires_at": "2026-09-23T12:30:00Z",
  "valid": true,
  "summary": {"inserted": 1, "updated": 0, "unchanged": 0},
  "errors": [],
  "warnings": [{"code": "historical_completion_date_proxy", "count": 1}]
}
```

Рекомендуемые ограничения MVP: 10 MiB на файл, 10 000 строк истории за один upload, UTF-8/UTF-8 BOM, запрет ZIP на публичном endpoint (локальная prepare-команда разбирает ZIP отдельно). Избыточный размер → 413; нарушения схемы → 422 с file/row/field; устаревшая revision/истёкший batch/конфликт → 409. Сервер не принимает пути к файлам от клиента. Staged файлы ограничены TTL 30 min, непрозрачный batch ID, содержимое хранится вне static root. При validation error можно вернуть preview с `valid=false`, но commit такого batch всегда запрещён.

## Состояния UI

Профиль загружается независимо от AI. Карточки различают loading, llm success, fallback с подписью «резервный расчёт», no_target, target_satisfied, no_candidates, network error, stale. Не показывать skeleton как готовую рекомендацию; кнопка complete блокируется локально на время запроса, но идемпотентность гарантирует сервер. Для будущего scheduled event можно записаться, но нельзя завершить раньше session_date; self_paced подходит для демонстрации замкнутого цикла.

## Что фиксируем перед стартом командной интеграции

1. Owner Backend реализует DTO этого API и добавляет OpenAPI; owner AI сохраняет семантику двух JSON Schema; owner Frontend использует общий fixture.
2. На контрактные изменения обновляются пример ответа и AGENTS.md в том же PR. Новое обязательное поле требует согласования всех трёх владельцев.
3. HTTP statuses, no-step state и revision проверяются contract tests backend; live LLM не нужен для CI frontend/backend.
