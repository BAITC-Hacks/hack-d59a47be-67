import { useLayoutEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  FileCheck2,
  FileJson,
  FileSpreadsheet,
  Info,
  LoaderCircle,
  ShieldCheck,
  Trash2,
  Upload,
} from "lucide-react";
import { ApiError, type ImportResponse, type SourceFile } from "../../api";
import { useApp } from "../../context";
import {
  importIssueDetails,
  readImportFile,
  type ImportFilename,
} from "./importFiles";
import "./import.css";

type Selection = { source: SourceFile; name: string; size: number };
type SelectedFiles = Partial<Record<ImportFilename, Selection>>;
type ValidatedImport = { response: ImportResponse; files: SourceFile[] };
type ImportPageProps = { onDone: () => void };

const FILE_OPTIONS = [
  {
    name: "employees.json" as const,
    title: "Профили сотрудников",
    description: "Имена, роли, навыки и карьерные цели.",
    accept: ".json,application/json",
    Icon: FileJson,
  },
  {
    name: "activity_history.csv" as const,
    title: "История активностей",
    description: "Назначения, участие и завершённые мероприятия.",
    accept: ".csv,text/csv",
    Icon: FileSpreadsheet,
  },
];

const COUNT_LABELS: Record<string, string> = {
  employees: "Новых сотрудников",
  history: "Новых записей истории",
  skills: "Навыков",
  role_profiles: "Профилей ролей",
  events: "Мероприятий",
};

export function ImportPage({ onDone }: ImportPageProps) {
  const { client, mode, user, handleError } = useApp();
  const [selected, setSelected] = useState<SelectedFiles>({});
  const [validated, setValidated] = useState<ValidatedImport | null>(null);
  const [result, setResult] = useState<ImportResponse | null>(null);
  const [busy, setBusy] = useState<"reading" | "preview" | "commit" | null>(
    null,
  );
  const [error, setError] = useState("");
  const [errorDetails, setErrorDetails] = useState<string[]>([]);
  const [uncertainCommit, setUncertainCommit] = useState(false);
  const operationInFlight = useRef(false);
  const lifecycle = useRef({ mounted: false, epoch: 0 });

  useLayoutEffect(() => {
    lifecycle.current = { mounted: true, epoch: lifecycle.current.epoch + 1 };
    operationInFlight.current = false;
    // Uploaded content and checked tokens belong to this account/connection.
    setSelected({});
    setValidated(null);
    setResult(null);
    setBusy(null);
    setError("");
    setErrorDetails([]);
    setUncertainCommit(false);
    return () => {
      lifecycle.current = {
        mounted: false,
        epoch: lifecycle.current.epoch + 1,
      };
      operationInFlight.current = false;
    };
  }, [client, mode, user.id]);

  const currentOperation = () => {
    const epoch = lifecycle.current.epoch;
    return () => lifecycle.current.mounted && lifecycle.current.epoch === epoch;
  };

  const isDemo = mode === "demo";
  const currentStep = result ? 3 : validated ? 2 : 1;
  const selectedCount = Object.keys(selected).length;

  const selectFile = async (
    file: File | undefined,
    expectedName: ImportFilename,
  ) => {
    if (!file || operationInFlight.current || isDemo) return;
    const isCurrent = currentOperation();
    operationInFlight.current = true;
    setBusy("reading");
    setError("");
    setErrorDetails([]);
    setValidated(null);
    setUncertainCommit(false);
    setSelected((previous) => {
      const next = { ...previous };
      delete next[expectedName];
      return next;
    });
    try {
      const source = await readImportFile(file, expectedName);
      if (!isCurrent()) return;
      setSelected((previous) => ({
        ...previous,
        [expectedName]: { source, name: file.name, size: file.size },
      }));
    } catch (cause) {
      if (!isCurrent()) return;
      setError(
        cause instanceof Error
          ? cause.message
          : "Не удалось прочитать файл. Попробуйте ещё раз.",
      );
    } finally {
      if (isCurrent()) {
        operationInFlight.current = false;
        setBusy(null);
      }
    }
  };

  const removeFile = (name: ImportFilename) => {
    if (operationInFlight.current) return;
    setSelected((previous) => {
      const next = { ...previous };
      delete next[name];
      return next;
    });
    setValidated(null);
    setUncertainCommit(false);
    setError("");
    setErrorDetails([]);
  };

  const validate = async () => {
    if (operationInFlight.current || isDemo || !selectedCount) return;
    const isCurrent = currentOperation();
    const files = FILE_OPTIONS.flatMap(({ name }) =>
      selected[name] ? [selected[name]!.source] : [],
    );
    operationInFlight.current = true;
    setBusy("preview");
    setError("");
    setErrorDetails([]);
    setValidated(null);
    setUncertainCommit(false);
    try {
      const response = await client.importFiles({ dry_run: true, files });
      if (!isCurrent()) return;
      if (response.status !== "validated" || !response.preview_token) {
        throw new Error(
          "Сервис не подтвердил проверку. Повторите её перед загрузкой.",
        );
      }
      setValidated({ response, files });
    } catch (cause) {
      if (!isCurrent()) return;
      setError(handleError(cause));
      setErrorDetails(importIssueDetails(cause, files));
    } finally {
      if (isCurrent()) {
        operationInFlight.current = false;
        setBusy(null);
      }
    }
  };

  const commit = async () => {
    if (
      operationInFlight.current ||
      isDemo ||
      !validated?.response.preview_token ||
      uncertainCommit
    )
      return;
    const isCurrent = currentOperation();
    operationInFlight.current = true;
    setBusy("commit");
    setError("");
    setErrorDetails([]);
    try {
      const response = await client.importFiles({
        dry_run: false,
        files: validated.files,
        preview_token: validated.response.preview_token,
      });
      if (!isCurrent()) return;
      if (response.status !== "imported")
        throw new Error("Сервис не подтвердил сохранение данных.");
      setResult(response);
      setValidated(null);
      setSelected({});
    } catch (cause) {
      if (!isCurrent()) return;
      const message = handleError(cause);
      setErrorDetails(importIssueDetails(cause, validated.files));
      if (cause instanceof ApiError && cause.status === 409) {
        setValidated(null);
        setError(
          "Данные изменились или срок проверки истёк. Файлы сохранены на этой странице — проверьте их ещё раз.",
        );
      } else {
        setError(message);
        // A lost response may hide a successful write. A fresh preview safely
        // reports already imported rows instead of encouraging a blind retry.
        setUncertainCommit(true);
      }
    } finally {
      if (isCurrent()) {
        operationInFlight.current = false;
        setBusy(null);
      }
    }
  };

  return (
    <div className="import-page">
      <button
        className="button button-ghost import-back"
        onClick={onDone}
        disabled={busy !== null}
      >
        <ArrowLeft size={16} aria-hidden="true" /> К команде
      </button>
      <header className="page-heading">
        <div>
          <p className="eyebrow">ДАННЫЕ КОМАНДЫ</p>
          <h1>Добавьте людей. Сохраните их путь.</h1>
          <p className="muted">
            Загрузите профили и историю активностей вместе. Сначала проверим
            данные, затем вы подтвердите сохранение.
          </p>
        </div>
      </header>

      <ol className="import-steps" aria-label="Этапы загрузки">
        {["Файлы", "Проверка", "Готово"].map((label, index) => (
          <li
            key={label}
            className={
              currentStep > index + 1
                ? "is-complete"
                : currentStep === index + 1
                  ? "is-current"
                  : ""
            }
            aria-current={currentStep === index + 1 ? "step" : undefined}
          >
            <span className="import-step-number">
              {currentStep > index + 1 ? (
                <Check size={15} aria-hidden="true" />
              ) : (
                index + 1
              )}
            </span>
            <span>{label}</span>
          </li>
        ))}
      </ol>

      {isDemo && (
        <div className="alert import-demo-note" role="status">
          <Info size={20} aria-hidden="true" />
          <div>
            <strong>Загрузка доступна после входа в рабочую систему</strong>
            <p>
              Демо помогает познакомиться с продуктом и не принимает ваши файлы.
              Для импорта войдите с учётной записью HR.
            </p>
          </div>
        </div>
      )}
      {error && (
        <div className="alert import-error" role="alert">
          <p>{error}</p>
          {errorDetails.length > 0 && (
            <ul>
              {errorDetails.map((detail, index) => (
                <li key={index}>{detail}</li>
              ))}
            </ul>
          )}
          {uncertainCommit && (
            <p>
              Не удалось подтвердить сохранение. Проверьте эти же файлы
              повторно: уже добавленные записи не будут загружены второй раз.
            </p>
          )}
        </div>
      )}

      {result ? (
        <section className="card import-success" aria-live="polite">
          <span className="import-success-icon">
            <CheckCircle2 size={35} aria-hidden="true" />
          </span>
          <p className="eyebrow">ДАННЫЕ СОХРАНЕНЫ</p>
          <h2>
            {result.imported_records
              ? "Команда готова к следующему шагу"
              : "Все записи уже были в системе"}
          </h2>
          <p className="muted">
            {result.imported_records
              ? "Откройте профиль сотрудника: навыки и история уже обновлены."
              : "Повторные записи не добавлены. Можно вернуться к профилям сотрудников."}
          </p>
          <ImportCounts response={result} />
          {(result.warnings ?? []).length > 0 && (
            <ul className="import-warnings">
              {result.warnings!.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          )}
          <button className="button button-primary" onClick={onDone}>
            Открыть команду <ArrowRight size={17} aria-hidden="true" />
          </button>
        </section>
      ) : (
        <div className="import-layout">
          <div className="import-main">
            <section
              className="card import-files"
              aria-labelledby="files-title"
            >
              <div className="import-section-heading">
                <span className="import-section-icon">
                  <Upload size={19} aria-hidden="true" />
                </span>
                <div>
                  <h2 id="files-title">Выберите файлы</h2>
                  <p className="muted">
                    Один или оба файла, до 2 МБ каждый, кодировка UTF-8.
                  </p>
                </div>
              </div>
              <div className="import-file-grid">
                {FILE_OPTIONS.map(
                  ({ name, title, description, accept, Icon }) => (
                    <div
                      className={`import-file-card${selected[name] ? " has-file" : ""}`}
                      key={name}
                    >
                      <div className="import-file-top">
                        <Icon size={24} aria-hidden="true" />
                        <span className="badge">
                          {name.endsWith(".json") ? "JSON" : "CSV"}
                        </span>
                      </div>
                      <h3>{title}</h3>
                      <p className="muted">{description}</p>
                      <label
                        className={`import-file-picker${isDemo || busy ? " is-disabled" : ""}`}
                      >
                        <Upload size={16} aria-hidden="true" />
                        <span>
                          {selected[name] ? "Заменить файл" : "Выбрать файл"}
                        </span>
                        <input
                          type="file"
                          accept={accept}
                          aria-label={`Выбрать ${name}`}
                          disabled={isDemo || busy !== null}
                          onChange={(event) => {
                            const file = event.currentTarget.files?.[0];
                            event.currentTarget.value = "";
                            void selectFile(file, name);
                          }}
                        />
                      </label>
                      {selected[name] ? (
                        <div className="import-selection">
                          <FileCheck2 size={15} aria-hidden="true" />
                          <span>
                            <strong>{selected[name]!.name}</strong>
                            <small>
                              {Math.max(
                                1,
                                Math.ceil(selected[name]!.size / 1_000),
                              )}{" "}
                              КБ · выбран
                            </small>
                          </span>
                          <button
                            className="button button-ghost import-remove"
                            onClick={() => removeFile(name)}
                            disabled={busy !== null}
                            aria-label={`Убрать ${name}`}
                          >
                            <Trash2 size={15} aria-hidden="true" />
                          </button>
                        </div>
                      ) : (
                        <span className="import-expected-name">{name}</span>
                      )}
                    </div>
                  ),
                )}
              </div>
              <div className="import-validation-action">
                <p className="muted">
                  При выборе файл остаётся в браузере. Кнопка «Проверить»
                  отправит его в рабочую систему без сохранения записей.
                </p>
                <button
                  className="button button-primary"
                  disabled={isDemo || busy !== null || !selectedCount}
                  onClick={() => void validate()}
                >
                  {busy === "preview" ? (
                    <>
                      <LoaderCircle
                        className="import-spinning"
                        size={16}
                        aria-hidden="true"
                      />{" "}
                      Проверяем…
                    </>
                  ) : (
                    <>
                      <ShieldCheck size={17} aria-hidden="true" />{" "}
                      {validated || uncertainCommit
                        ? "Проверить повторно"
                        : "Проверить данные"}
                    </>
                  )}
                </button>
              </div>
            </section>

            {validated && (
              <section
                className="card import-preview"
                aria-labelledby="preview-title"
                aria-live="polite"
              >
                <div className="import-section-heading">
                  <span className="import-section-icon">
                    <CheckCircle2 size={20} aria-hidden="true" />
                  </span>
                  <div>
                    <h2 id="preview-title">Проверка пройдена</h2>
                    <p className="muted">
                      {validated.response.imported_records
                        ? "Всё готово. Проверьте итог и подтвердите сохранение."
                        : "Новых записей нет. Все выбранные записи уже есть в системе."}
                    </p>
                  </div>
                </div>
                <ImportCounts response={validated.response} />
                {(validated.response.warnings ?? []).length > 0 && (
                  <div className="import-warning-panel">
                    <strong>Обратите внимание</strong>
                    <ul>
                      {validated.response.warnings!.map((warning, index) => (
                        <li key={index}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="import-confirm-action">
                  <p className="muted">
                    Проверка действует 15 минут. Изменение файлов или данных
                    потребует новой проверки.
                  </p>
                  <button
                    className="button button-primary"
                    onClick={() => void commit()}
                    disabled={busy !== null || uncertainCommit}
                  >
                    {busy === "commit" ? (
                      <>
                        <LoaderCircle
                          className="import-spinning"
                          size={16}
                          aria-hidden="true"
                        />{" "}
                        Сохраняем…
                      </>
                    ) : (
                      <>
                        <Check size={17} aria-hidden="true" /> Подтвердить
                        загрузку
                      </>
                    )}
                  </button>
                </div>
              </section>
            )}
            <span className="import-status" role="status">
              {busy === "reading"
                ? "Читаем файл…"
                : busy === "preview"
                  ? "Проверяем содержимое и связи между записями…"
                  : busy === "commit"
                    ? "Сохраняем проверенные данные…"
                    : ""}
            </span>
          </div>

          <aside className="card import-guide">
            <span className="import-guide-icon">
              <ShieldCheck size={23} aria-hidden="true" />
            </span>
            <h2>История остаётся целой</h2>
            <p className="muted">Система проверяет все записи до сохранения.</p>
            <ul>
              <li>
                <Check size={16} aria-hidden="true" />
                <span>
                  Профили и история проверяются вместе — можно добавить нового
                  сотрудника сразу с его активностями.
                </span>
              </li>
              <li>
                <Check size={16} aria-hidden="true" />
                <span>
                  Одинаковые записи не дублируются. Выполненные в системе
                  действия сохраняются.
                </span>
              </li>
              <li>
                <Check size={16} aria-hidden="true" />
                <span>
                  Если найдена ошибка, вся загрузка отменяется. Исправьте
                  исходный файл и повторите проверку.
                </span>
              </li>
            </ul>
            <div className="import-guide-note">
              <strong>Нужно исправить существующий профиль?</strong>
              <p>
                Этот импорт добавляет новые записи. Изменённые записи с тем же
                ID будут отклонены.
              </p>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

function ImportCounts({ response }: { response: ImportResponse }) {
  const entries = Object.entries(response.counts ?? {}).filter(
    ([key, count]) => key === "employees" || key === "history" || count > 0,
  );
  return (
    <dl className="import-counts">
      {entries.map(([key, count]) => (
        <div key={key}>
          <dt>{COUNT_LABELS[key] ?? key}</dt>
          <dd>{count.toLocaleString("ru-RU")}</dd>
        </div>
      ))}
    </dl>
  );
}
