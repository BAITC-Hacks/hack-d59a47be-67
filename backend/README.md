# Backend: целевая структура

> **Актуальный приоритет (2026-09-23).** Действующие согласованные контракты: [API v1](../contracts/API.md), [AI v1](../contracts/AI_CONTRACT.md); единственные схемы — [backend/app/contracts.py](../backend/app/contracts.py). Изменения [согласованы владельцем задачи](../docs/CONTRACT_CHANGE_PROPOSAL.md). Prefix — `/api`, версия состояния — `state_version` / `expected_state_version`, дата сценария — `scenario_date`; ответ AI — `RecommendationResult`, HTTP-карточки — `RecommendationResponse`. История имеет `date_source`, импорт — один endpoint `/api/hr/import` с preview token, завершение — `/api/employees/{id}/completions` с `Idempotency-Key`. Старые `data_revision`, `/api/v1`, participation endpoints, fallback и дополнительные поля ниже не являются действующим контрактом или обещанием реализации. Проверенные команды и ограничения — [HANDOFF](../docs/HANDOFF.md) и [VERIFICATION](../docs/VERIFICATION.md).

Алихан — backend, данные, API, права, интеграция и деплой (`backend/`, кроме `backend/app/ai/`, общие `contracts/` и инфраструктура); Олег — AI-ядро `backend/app/ai/` и его тесты; Батыр — `frontend/`.

## Историческое предложение

Следующий текст сохранён для контекста проектирования; он не переопределяет ссылки и владельцев выше. Названия таблиц, будущая структура, состояния и приёмочные предложения нужно сверять с действующим кодом и контрактом.

Приложение ещё не реализовано. Выбран Python + FastAPI, SQLAlchemy + Alembic, SQLite для demo. Точные версии и lock-файл добавляет владелец backend перед первым vertical slice.

```text
backend/app/
  main.py                 # composition root / app factory
  api/                    # HTTP, auth dependencies, DTO
  domain/                 # replay, targets, gaps, eligibility, completion
  repositories/           # SQLAlchemy models / transactions
  imports/                # validate -> stage -> atomic commit
  ai/                     # зона Олега: prepared context -> validated result
  reporting/              # HR read models / definitions
backend/tests/
```

Domain не зависит от LLM, FastAPI и UI. AI-модуль не читает БД, не начисляет навыки и не предоставляет модели write tools. Все изменяемые зависимости (clock, provider, repository) передаются через composition root.

Первый результат Алихана: профиль из кита с правильным replay + eligibility, импорт дополнительного профиля и истории, идемпотентное завершение, server auth. Первый результат Олега: валидный ответ для prepared context по `contracts/`.

См. [архитектуру](../docs/architecture.md), [HTTP-контракт](../docs/api-contract.md), [модель](../docs/data-model.md).
