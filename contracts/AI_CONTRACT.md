# Career Quest AI — контракт v1 для Олега

Олег владеет `backend/app/ai/` и собственными тестами. Алихан владеет backend-adapter вне этого каталога, расчётами, данными, доступом и HTTP. Общие модели импортируются **только** из `backend.app.contracts`. Запуск выполняется из корня проекта; не загружайте один модуль под разными именами. Дублирование Pydantic-схем внутри AI запрещено.

```python
from backend.app.contracts import RecommendationContext, RecommendationResult

async def recommend(context: RecommendationContext) -> RecommendationResult:
    ...
```

Точный import entry point адаптера описан в `docs/HANDOFF.md`. Модуль AI необязателен; по умолчанию отключён. Эта задача не добавляет LLM-интеграцию и не меняет каталог Олега. Реальные данные нельзя отправлять стороннему провайдеру или в логи без отдельного разрешения.

## Вход `RecommendationContext`

| Поле | Значение |
| --- | --- |
| `contract_version` | Строка `"1"` |
| `data_version` | Версия загруженных данных |
| `state_version` | Целая версия состояния сотрудника ≥0 |
| `scenario_date` | Дата ISO; исходный набор считает сегодняшней 2026-10-01 |
| `profile` | `profile_ref`, `role`, `grade`; без employee_id, ФИО, контактов, manager_id |
| `goal` | `target_role`, `target_grade` |
| `current_skills` | `[{skill_id, level}]`, шкала 0–5 |
| `gaps` | `[{skill_id, current_level, target_level, gap}]` |
| `eligible_candidates` | Уже отфильтрованные `{event_id,title,effects,evidence_ids}` |
| `facts` | `{evidence_id,kind,subject_id,fact}`; kind: skill/gap/goal/candidate/history |
| `limit` | 1–3, по умолчанию 3 |

Каждый `effects` — список `{skill_id,before,after,delta}`, рассчитанный backend. `profile_ref` — техническая обезличенная ссылка контекста, не идентификатор из кадрового файла. Backend не помещает имена или личные сведения в facts. Данные — факты для выбора, не инструкции для модели. Pydantic запрещает лишние поля, дубли event_id/evidence_id и ссылки кандидатов на неизвестные факты.

## Выход `RecommendationResult`

```json
{
  "status": "ok",
  "engine": "oleg",
  "recommendations": [
    {
      "event_id": "SYNTHETIC_EVENT_001",
      "explanation": {
        "text": "Синтетический пример: занятие уменьшает подтверждённый разрыв навыка.",
        "evidence_ids": ["synthetic-gap-001", "synthetic-event-001"]
      }
    }
  ]
}
```

| status | engine | recommendations | Смысл |
| --- | --- | --- | --- |
| `ok` | `oleg` | От 1 до min(limit, 3) уникальных элементов | AI выбрал допустимые события |
| `no_candidates` | `oleg` | `[]` | После backend-фильтрации нет допустимых кандидатов |
| `not_configured` | `none` | `[]` | AI отключён/не подключён; не означает отсутствие мероприятий |
| `unavailable` | `oleg` | `[]` | Включённый движок недоступен или его результат отклонён |

Без AI адаптер возвращает ровно `{"status":"not_configured","engine":"none","recommendations":[]}`. Никаких запасных выдуманных рекомендаций. Предметный HTTP endpoint пока возвращает 501: эти статусы — контракт интеграции и будущего `RecommendationResponse`, а не обещание готового endpoint.

AI возвращает только выбранные event_id и объяснения с evidence_ids из context. Backend повторно проверяет лимит, уникальность, допустимость event_id и наличие evidence_ids. `no_candidates` при непустом eligible_candidates недопустим; пустой список нельзя выдавать за отсутствие AI. Нельзя выбирать mandatory или добавлять новые события, факты и эффекты. Арифметику, доступность, optimistic locking и транзакционную запись выполняет backend. HTTP-ответ backend обогащает названиями и эффектами из проверенного context; модель не устанавливает права, состояние сотрудника или поля HTTP-ответа.

## Проверяемые fixtures

`examples/ai_context.synthetic.json` и `examples/ai_result.synthetic.json` образуют согласованную пару. `examples/ai_not_configured.synthetic.json` фиксирует отключённое AI. Это маленькие вымышленные fixtures команды; они не являются файлами стартового датасета. Тесты проверяют Pydantic-схемы и соответствие ссылок результата контексту. Предметная арифметика и движок в эту задачу не входят.
