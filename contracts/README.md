# Единый контракт Career Quest v1

Действующий контракт согласован владельцем задачи с потребителями; решение записано в [CONTRACT_CHANGE_PROPOSAL.md](../docs/CONTRACT_CHANGE_PROPOSAL.md). Единственный источник моделей — [backend/app/contracts.py](../backend/app/contracts.py). Владельцы: Алихан — backend и общий контракт; Олег — `backend/app/ai/`; Батыр — `frontend/`.

- [API.md](API.md) — HTTP `/api`, `state_version`, cookie/CSRF, preview/completion, импорт и статусы.
- [AI_CONTRACT.md](AI_CONTRACT.md) — неизменный Python-вызов `async recommend(context: RecommendationContext) -> RecommendationResult`.
- [openapi.json](openapi.json) — экспорт FastAPI, команда `scripts/export-openapi`.
- [recommendation-context.schema.json](recommendation-context.schema.json) и [recommendation-result.schema.json](recommendation-result.schema.json) — **генерируемые** JSON Schema текущих Python-моделей, не отдельная редакция контракта.
- [ai_context.synthetic.json](examples/ai_context.synthetic.json) и [ai_result.synthetic.json](examples/ai_result.synthetic.json) — каноническая синтетическая пара. Старые пути `examples/recommendation-context.json` и `examples/recommendation-result.json` сохраняются как проверяемые генерируемые копии этой пары.

Проверка: `.venv/bin/python scripts/check_contracts.py`. После согласованного изменения Python-моделей: `.venv/bin/python scripts/check_contracts.py --write`, затем `scripts/test`. Проверка использует Pydantic, контролирует актуальность generated schemas, примеров и ссылок на кандидатов/evidence; отдельные схемы вручную не пишутся. JSON Schema не выражает все Python-валидаторы и не заменяет серверную семантическую проверку. Проверка не вызывает AI.

## Историческое предложение — заменено действующим контрактом

Ниже сохранён прежний текст для истории проектирования. Упомянутые `context_id`, `data_revision`, `$defs/modelOutput`, fallback и минимум три категории не являются полями или ограничениями текущего Python-контракта. Схемы по ссылкам выше теперь сгенерированы из единственного актуального источника; прежние версии доступны в Git history. При противоречии используются API.md, AI_CONTRACT.md и contracts.py.

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
