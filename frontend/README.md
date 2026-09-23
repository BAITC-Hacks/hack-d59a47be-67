# Frontend: целевая структура

Приложение ещё не реализовано. Выбран React + TypeScript + Vite. Сначала верстать и проверять сценарии на согласованном синтетическом ответе, затем заменить adapter на HTTP API без переписывания экранов.

```text
frontend/src/
  api/                    # API client / generated types / fixture adapter
  features/profile/       # current skills, history, target readiness
  features/recommendations/ # cards, evidence, loading/fallback/empty/stale
  features/progress/      # completion confirmation + before/after
  features/hr/            # gaps, no-step list, participation
  features/import/        # HR upload, validation preview, commit
  components/             # shared presentational UI
```

UI не вычисляет gain, eligibility и HR permissions. Session identity и ограничения приходят с backend. У сотрудника нет маршрута со списком чужой вовлечённости. Прогресс показывает соответствие требованиям цели, не гарантированное повышение.

Первый результат участника 3: профиль → объяснимая рекомендация → завершение → обновлённый прогресс, плюс HR и импорт с ошибкой/preview. Стартовый fixture AI-модуля: `contracts/examples/`; HTTP envelope объяснений описан в [контракте API](../docs/api-contract.md).
