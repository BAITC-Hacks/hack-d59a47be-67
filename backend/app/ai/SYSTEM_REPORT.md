# Проверка актуальной системы — 2026-09-23

Общий сценарий работает на актуальных backend, AI и frontend. Штатная контейнерная проверка проходит без временных исправлений. Исправления русских объяснений и мобильной вёрстки подтверждены в реальном браузере. Это локальная проверка Linux ARM64 и вымышленных данных; production и SLA не подтверждены.

## Версии и обновление веток

Выполнен `git fetch --all --prune`. Все семь удалённых веток представлены локальными tracking-ветками; существующие отстававшие ветки обновлены только fast-forward, остальные уже совпадали с origin. Чужие commits/рабочие копии не перезаписаны; PR и ветки не объединялись.

| Компонент | Проверенный commit |
|---|---|
| Backend, `codex/backend-ai-handoff` | `3b85babc78e6c522b6c5d04d42e5a16a37966570` |
| AI, `codex/ai-recommendations` | `f10006a13c056ae49983c6e8200711cea73afa6a` |
| Frontend, `feat/frontend-app` | `f91638b612dfc36b06b399fdbb426f51c1ad248c` |
| Architecture | `e3dfc7e918387d660222af9600e6d0ab83cd252b` |
| Main | `779ec87` |

Backend/AI объединены только через `git merge-tree --write-tree` в виртуальное дерево `30bb15b961516ba45ca2fa8f49de1beb700cec0f`; refs не изменялись. Для UI в отдельный экспорт этого дерева добавлен каталог `frontend/` ровно из указанного frontend commit. Runtime исходники не правились. Последующий commit этого отчёта меняет только документацию/QA-артефакты AI и AGENTS.md.

## Автоматические проверки

Из detached backend worktree выполнено:

```sh
PYTHON='/Users/kim/IdeaProjects/Career Quest/.venv-ai/bin/python' \
  scripts/verify-integration --ai-ref origin/codex/ai-recommendations --container
```

Для запуска из другой ветки, содержащей runner, укажите также `--backend-ref origin/codex/backend-ai-handoff`. Скрипт проверяет закоммиченные refs, а не незакоммиченные файлы.

| Проверка | Результат |
|---|---|
| Исходные synthetic unit tests | 21 PASS |
| Backend/AI pytest | 468 PASS |
| Explicit `oleg_backend_handoff_acceptance.py` | 19 PASS |
| Generated schemas / fixtures / references | 9 PASS |
| Ruff | PASS |
| AI TCP smoke | Два запуска; facts/cards/latest, права, session, restart и точный idempotency replay PASS |
| Docker checker | Build, non-root, allowlist, пустой `/data`, AI import, права, миграции, volume и replay после пересоздания PASS |
| Frontend | 52 tests, TypeScript/build, Prettier PASS |
| Контракт frontend | OpenAPI backend совпадает с `contracts/openapi.json`; сгенерированный TypeScript совпадает с `schema.ts` побайтно |
| Frontend HTTP smoke | Настоящий API, временная synthetic SQLite; сессии, права/CSRF, completion, импорт, stale, version conflict и restart PASS |

Обычные TCP/container/frontend smoke используют тестовый selector либо отключённый AI. Сеть OpenAI проверена отдельно ниже. Единственное предупреждение Python suites — существующий Starlette/httpx deprecation.

Docker Engine 28.0.4, Compose 2.34.0, Linux ARM64, Python в контейнере 3.12.14. Host Python 3.12.11. Frontend: Node 22.14.0, установленный pnpm 11.19.0 при заявленном 11.25.0; frozen-lock install, тесты и сборка прошли. Сборка именно pnpm 11.25.0 здесь не проверена.

## Настоящий AI и браузер

Использован ранее разрешённый локальный OpenAI ключ; он передан только runtime-процессу через окружение. Исходный кит, реальные профили, `.env` и секреты не отправлялись и не включались в образ/отчёт. Модель `gpt-4.1-mini-2025-04-14`, prompt `career-quest-selection-v1`.

Внутри свежего контейнера `python -m backend.app.ai.backend_evaluation --live` получил контексты через синтетический импорт → SQLite → настоящий `CareerService._context`:

| Сценарий | Результат | Время |
|---|---|---|
| Критичный навык против минимального некритичного | PASS, событие B | 1617,30 ms |
| Повторные пропуски формата при наличии альтернативы | PASS, событие B | 2397,09 ms |
| Отсутствие истории | PASS, событие A | 994,49 ms |

Отдельный реальный UI → контейнер → OpenAI запрос вернул `ok/oleg` и три рекомендации; HTTP duration по browser Performance API — **5033,10 ms**. Все карточки имели пять evidence IDs и русские объяснения вместо машинного JSON. Пример и перенос проверены визуально: [desktop](verification/current-desktop.png), [mobile](verification/current-mobile.png).

В браузере пройден обычный вход синтетического сотрудника, профиль, запрос рекомендаций, preview, подтверждённое тестовое завершение вымышленной активности `UI_SYNTH_SYSTEMS_COURSE` (mode=`completion`), обновление профиля и перезапуск контейнера:

- Навык проектирования систем: 2 → 3; прогресс: 56,25% → 62,5%; state_version: 1 → 2.
- После `docker restart` и reload: `/api/me`, профиль и latest — 200; история, навыки и тексты карточек совпали с сохранёнными до restart.
- Карточки помечены stale; все три кнопки прежнего preview отключены, показано предложение обновить подборку.
- При раскрытом объяснении ширины viewport/scrollWidth: 390/390, 320/320, 1440/1440. Горизонтального переполнения нет.
- До первого подбора ожидаемый `latest` 404 корректно отображался пустым состоянием. Диагностический запрос проверяющего к несуществующему `/api/auth/me` дал 404; после исправления адреса `/api/me` вернул 200. Это не запрос интерфейса.

Безопасные числовые результаты, hashes и версии: [system_verification.json](system_verification.json). Image ID UI-проверки: `sha256:7a447cfe9dfee04e464fc1e9c20670f46269809404a434e606cabac9a6f601ad`.

## Закрытые проблемы и оставшиеся границы

Оба прежних Docker-блокера (`evidence == 4` и включение QA-файлов в image) устранены backend-владельцем и проверены штатной командой. Исторический `container-fixes.patch` повторно применять не нужно. Прежние P2 «машинные объяснения» и «mobile overflow» также исправлены владельцами и закрыты этим прогоном. AI/backend/frontend runtime в этой проверке не менялись.

Один UI-запрос превысил 5 секунд на 33,10 ms: выполнение порога ≤5 s и p95 не заявляются; для SLA нужен отдельный замер с согласованной метрикой. Три quality cases не заменяют скрытую приёмку и оценку стабильности. Русские backend-шаблоны пока не локализованы; старые сохранённые карточки получают новые тексты только после повторной генерации. Полная HR-аналитика пробелов/участия остаётся незавершённой по документации frontend; текущий прогон не добавляет её.

GitHub CI по-прежнему не стартует: свежая annotation backend check-run `107163472658` сообщает `The job was not started because your account is locked due to a billing issue.` Локальные PASS не означают GitHub CI PASS. Нужны снятие billing lock владельцем и реальный повторный запуск.

Тестовые контейнер/volume/UI-образ, случайные credentials, Vite и browser очищены адресно. Чужие ресурсы, рабочая БД и `.env` сохранены. Временный экспорт исходников остаётся локально для диагностики, серверы остановлены. Docker Desktop оставлен доступным. Публичного deploy и merge PR не было. Следующий командный шаг — review/согласованная интеграция трёх веток и подготовка общей демо-среды.
