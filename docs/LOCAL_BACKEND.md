# Локальный backend

Актуальные команды запуска, импорта, создания серверных учётных записей, тестов и интеграции приведены в [HANDOFF.md](HANDOFF.md). Проверенные результаты — [VERIFICATION.md](VERIFICATION.md). Один backend/процесс/SQLite volume; AI по умолчанию явно отключён.

```sh
scripts/dev
make check
scripts/smoke
```

Командный repository: BAITC-Hacks/hack-d59a47be-67; base PR — codex/architecture-foundation. Не пушить и не объединять изменения в main/архитектурную ветку напрямую.
