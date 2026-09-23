import { describe, expect, it } from "vitest";
import { ApiError } from "../../api";
import {
  importIssueDetails,
  MAX_FILE_BYTES,
  readImportFile,
} from "./importFiles";

function fileWithBytes(bytes: Uint8Array, name: string) {
  const file = new File([bytes as BlobPart], name);
  Object.defineProperty(file, "arrayBuffer", {
    value: () => Promise.resolve(bytes.buffer),
  });
  return file;
}

describe("source file reading", () => {
  it("preserves the original UTF-8 BOM, Cyrillic text and CRLF for server validation", async () => {
    const content = '\uFEFF{"meta":{},"employees":[{"full_name":"Тест"}]}\r\n';
    const source = await readImportFile(
      fileWithBytes(new TextEncoder().encode(content), "employees.json"),
      "employees.json",
    );
    expect(source).toEqual({
      source_filename: "employees.json",
      source_format: "json",
      content,
    });
  });

  it("rejects invalid encoding instead of silently replacing characters", async () => {
    await expect(
      readImportFile(
        fileWithBytes(new Uint8Array([0xff]), "employees.json"),
        "employees.json",
      ),
    ).rejects.toThrow("UTF-8");
  });

  it("rejects files above the documented limit before reading them", async () => {
    const file = new File(["x"], "employees.json");
    Object.defineProperty(file, "size", { value: MAX_FILE_BYTES + 1 });
    await expect(readImportFile(file, "employees.json")).rejects.toThrow(
      "2 МБ",
    );
  });

  it("shows safe validation locations without echoing raw input values", () => {
    const error = new ApiError(422, "invalid_source", "Invalid", undefined, {
      file_index: 0,
      errors: [
        {
          location: ["employees", 2, "full_name"],
          type: "missing",
          input: "do-not-display",
        },
      ],
    });
    const notes = importIssueDetails(error, [
      {
        source_filename: "employees.json",
        source_format: "json",
        content: "private content",
      },
    ]);
    expect(notes).toEqual([
      "Файл: employees.json",
      "employees → запись 3 → full_name: обязательное поле отсутствует.",
    ]);
    expect(notes.join("")).not.toContain("private");
    expect(notes.join("")).not.toContain("do-not-display");
  });
});
