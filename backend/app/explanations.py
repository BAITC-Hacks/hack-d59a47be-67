"""Russian UI copy rendered only from backend-verified values, never model prose."""

from datetime import date
from decimal import Decimal

GRADES = {"Junior": "начальный", "Middle": "средний", "Senior": "старший", "Lead": "ведущий"}
FORMATS = {"online": "онлайн", "offline": "очно", "self_paced": "самостоятельное обучение"}
TYPES = {
    "compliance": "комплаенс-обучение",
    "onboarding": "адаптация",
    "course": "курс",
    "workshop": "практикум",
    "mentoring": "наставничество",
    "certification": "сертификация",
    "meetup": "профессиональная встреча",
}
STATUSES = {
    "completed": "завершено",
    "in_progress": "в процессе",
    "dropped": "прекращено",
    "no_show": "пропущено",
    "declined": "отказов",
    "overdue": "просрочено",
}


def number(value):
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def day(value):
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def goal_text(employee, target):
    return (
        f"Ваша цель — «{target['role']}», {GRADES[target['grade']]} уровень. "
        f"Сейчас ваш уровень — {GRADES[employee['grade']]}."
    )


def gap_text(gap, name, critical):
    text = (
        f"Навык «{name}»: сейчас {number(gap['current_level'])} из 5, "
        f"для цели нужно {number(gap['target_level'])}."
    )
    return text + (" Это ключевой навык для вашей цели." if critical else "")


def candidate_text(event):
    return (
        f"Формат — {FORMATS[event['format']]}; нагрузка — {number(event['duration_hours'])} ч. "
        "Мероприятие доступно для вашей текущей роли и уровня с учётом требований и расписания."
    )


def history_text(summary):
    if summary["scope"] == "event":
        scope = "По этому мероприятию"
    elif summary["scope"] == "same_type_format_other_events":
        scope = (
            f"По другим мероприятиям того же типа ({TYPES[summary['event_type']]}) "
            f"и формата ({FORMATS[summary['event_format']]})"
        )
    else:
        scope = "По всей доступной истории участия"
    cutoff = day(summary["as_of"])
    if not summary["sample_size"]:
        return f"{scope} на {cutoff} записей нет. Это не говорит о ваших предпочтениях."
    first, last = day(summary["observed_from"]), day(summary["observed_to"])
    period = first if first == last else f"{first}–{last}"
    counts = "; ".join(
        f"{label} — {summary['status_counts'][status]}"
        for status, label in STATUSES.items()
        if summary["status_counts"][status]
    )
    text = f"{scope} записей: {summary['sample_size']} (даты: {period}; срез: {cutoff}); {counts}."
    proxy = summary["date_sources"]["historical_proxy"]
    exact = summary["date_sources"]["completed_at"]
    if proxy:
        text += f" Приблизительных исторических дат: {proxy}."
    if exact:
        text += f" Записей с временем завершения: {exact}."
    if proxy:
        text += " По приблизительным датам нельзя оценить соблюдение сроков."
    return text + " Эти записи не определяют ваши предпочтения и могут не отражать всю историю."


def render(evidence, display_facts):
    # Keep a request-local mapping; concurrent profiles must never share UI facts.
    # Duplicate goal-list parts may share UI wording, while all evidence IDs remain in the response.
    return " ".join(dict.fromkeys(display_facts[fact.evidence_id] for fact in evidence))
