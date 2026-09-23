# Backend: целевая структура

Приложение ещё не реализовано. Выбран Python + FastAPI, SQLAlchemy + Alembic, SQLite для demo. Точные версии и lock-файл добавляет владелец backend перед первым vertical slice.

```text
backend/app/
  main.py                 # composition root / app factory
  api/                    # HTTP, auth dependencies, DTO
  domain/                 # replay, targets, gaps, eligibility, completion
  repositories/           # SQLAlchemy models / transactions
  imports/                # validate -> stage -> atomic commit
  recommendations/        # зона участника 1: prepared context -> validated result
  reporting/              # HR read models / definitions
backend/tests/
```

Domain не зависит от LLM, FastAPI и UI. AI-модуль не читает БД, не начисляет навыки и не предоставляет модели write tools. Все изменяемые зависимости (clock, provider, repository) передаются через composition root.

Первый результат участника 2: профиль из кита с правильным replay + eligibility, импорт дополнительного профиля и истории, идемпотентное завершение, server auth. Первый результат участника 1: валидный ответ для prepared context по `contracts/`.

См. [архитектуру](../docs/architecture.md), [HTTP-контракт](../docs/api-contract.md), [модель](../docs/data-model.md).
