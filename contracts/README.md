# Контракты AI v1.0

Внутренний вход/выход recommendation service; HTTP envelope описан отдельно в [api-contract.md](../docs/api-contract.md).

| Артефакт | Смысл |
|---|---|
| [recommendation-context.schema.json](recommendation-context.schema.json) | Backend → AI: навыки, цель, разрешённые кандидаты, признаки, типизированные evidence |
| [recommendation-result.schema.json](recommendation-result.schema.json) | AI service → backend: validated result; `$defs/modelOutput` — ограниченный ответ LLM |
| [examples/recommendation-context.json](examples/recommendation-context.json) | Полностью вымышленный prepared context с конфликтом минимального и критичного навыка |
| [examples/recommendation-result.json](examples/recommendation-result.json) | Согласованный результат с честным provenance fallback, не live AI |

JSON Schema Draft 2020-12 проверяет структуру; membership, математика фактов, актуальность revision и минимум три различные категории evidence требуют отдельного **ещё не реализованного** semantic validator. Полная спецификация: [ai-recommendations.md](../docs/ai-recommendations.md).

После `make setup-dev` запустите `.venv/bin/python scripts/check_contracts.py`. Это проверка схем и fixtures, не тест готового продукта или качества реальной модели. При изменении контракта обновите обе стороны, fixtures, документацию и AGENTS.md в одной порции работы.
