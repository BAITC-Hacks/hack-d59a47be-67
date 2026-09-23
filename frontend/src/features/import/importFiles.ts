import { ApiError, type SourceFile } from "../../api";

export const MAX_FILE_BYTES = 2_000_000;
export type ImportFilename = "employees.json" | "activity_history.csv";

export async function readImportFile(
  file: File,
  expectedName: ImportFilename,
): Promise<SourceFile> {
  if (file.name !== expectedName) {
    throw new Error(`Выберите файл с именем ${expectedName}.`);
  }
  if (file.size === 0) throw new Error("Файл пуст. Выберите файл с данными.");
  if (file.size > MAX_FILE_BYTES)
    throw new Error("Файл слишком большой. Максимальный размер — 2 МБ.");
  let content: string;
  try {
    // Keep the original BOM and line endings: the server validates the source text.
    content = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
      await file.arrayBuffer(),
    );
  } catch {
    throw new Error(
      "Не удалось прочитать файл. Сохраните его в кодировке UTF-8 и выберите снова.",
    );
  }
  return {
    source_filename: expectedName,
    source_format: expectedName.endsWith(".json") ? "json" : "csv",
    content,
  };
}

export function importIssueDetails(
  cause: unknown,
  files: SourceFile[],
): string[] {
  if (!(cause instanceof ApiError)) return [];
  const notes: string[] = [];
  const { details } = cause;
  if (
    typeof details.file_index === "number" &&
    Number.isInteger(details.file_index)
  ) {
    const file = files[details.file_index];
    if (file) notes.push(`Файл: ${file.source_filename}`);
  }
  if (typeof details.field === "string")
    notes.push(`Проверьте поле: ${details.field}`);
  const issues = Array.isArray(details.errors)
    ? details.errors
    : Array.isArray(details.fields)
      ? details.fields
      : [];
  for (const issue of issues.slice(0, 8)) {
    if (!issue || typeof issue !== "object" || !Array.isArray(issue.location))
      continue;
    const path = issue.location
      .filter(
        (part: unknown) => typeof part === "string" || typeof part === "number",
      )
      .map((part: string | number) =>
        typeof part === "number" ? `запись ${part + 1}` : part,
      )
      .join(" → ");
    if (!path) continue;
    const hint =
      issue.type === "missing"
        ? "обязательное поле отсутствует"
        : issue.type === "extra_forbidden"
          ? "лишнее поле в исходном файле"
          : issue.type === "enum" || issue.type === "literal_error"
            ? "значение не входит в разрешённый список"
            : "проверьте формат и значение";
    notes.push(`${path}: ${hint}.`);
  }
  if (issues.length > 8)
    notes.push(
      `Ещё ошибок: ${issues.length - 8}. Исправьте файл и повторите проверку.`,
    );
  return notes;
}
