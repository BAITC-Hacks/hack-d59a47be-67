import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type CareerClient, type ImportResponse } from "../../api";
import { AppContext, type AppContextValue } from "../../context";
import { ImportPage } from "./ImportPage";

afterEach(cleanup);

const preview: ImportResponse = {
  dry_run: true,
  status: "validated",
  data_version: "synthetic-1",
  imported_records: 1,
  preview_token: "synthetic-preview-token",
  counts: { employees: 1, history: 0 },
  warnings: [],
  revision: 1,
};

function sourceFile(
  content = '{"meta":{},"employees":[]}',
  name = "employees.json",
) {
  const bytes = new TextEncoder().encode(content);
  const file = new File([bytes], name, { type: "application/json" });
  Object.defineProperty(file, "arrayBuffer", {
    value: () => Promise.resolve(bytes.buffer),
  });
  return file;
}

function setup(mode: "live" | "demo" = "live") {
  const importFiles = vi.fn<CareerClient["importFiles"]>();
  const onDone = vi.fn();
  const context: AppContextValue = {
    client: { importFiles } as unknown as CareerClient,
    catalog: null,
    mode,
    user: { id: "test-hr", username: "test-hr", role: "hr", employee_id: null },
    handleError: vi.fn((cause) =>
      cause instanceof Error ? cause.message : "Ошибка",
    ),
  };
  const view = render(
    <AppContext.Provider value={context}>
      <ImportPage onDone={onDone} />
    </AppContext.Provider>,
  );
  return { importFiles, onDone, user: userEvent.setup(), context, ...view };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

async function selectAndValidate(user: ReturnType<typeof userEvent.setup>) {
  await user.upload(
    screen.getByLabelText("Выбрать employees.json"),
    sourceFile(),
  );
  await user.click(screen.getByRole("button", { name: "Проверить данные" }));
  await screen.findByRole("heading", { name: "Проверка пройдена" });
}

describe("two-phase HR import", () => {
  it("uploads only on an explicit check and submits the identical source with its token once", async () => {
    const { importFiles, user } = setup();
    importFiles.mockResolvedValueOnce(preview);
    let finishCommit: (value: ImportResponse) => void = () => {};
    importFiles.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishCommit = resolve;
        }),
    );
    const file = sourceFile('\uFEFF{"meta":{},"employees":[]}\r\n');
    await user.upload(screen.getByLabelText("Выбрать employees.json"), file);
    expect(importFiles).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Проверить данные" }));
    const confirm = await screen.findByRole("button", {
      name: "Подтвердить загрузку",
    });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(importFiles).toHaveBeenCalledTimes(2);
    const checked = importFiles.mock.calls[0][0];
    const committed = importFiles.mock.calls[1][0];
    expect(checked.dry_run).toBe(true);
    expect(committed).toEqual({
      dry_run: false,
      files: checked.files,
      preview_token: preview.preview_token,
    });
    expect(committed.files[0].content).toBe(
      '\uFEFF{"meta":{},"employees":[]}\r\n',
    );
    await act(async () =>
      finishCommit({ ...preview, status: "imported", dry_run: false }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "Команда готова к следующему шагу",
      }),
    ).toBeInTheDocument();
  });

  it("invalidates the checked batch when a file changes", async () => {
    const { importFiles, user } = setup();
    importFiles.mockResolvedValue(preview);
    await selectAndValidate(user);
    await user.upload(
      screen.getByLabelText("Выбрать employees.json"),
      sourceFile('{"meta":{},"employees":[{}]}'),
    );
    expect(
      screen.queryByRole("button", { name: "Подтвердить загрузку" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Проверить данные" }),
    ).toBeEnabled();
    expect(importFiles).toHaveBeenCalledTimes(1);
  });

  it("retains selected files and requires another check after a stale token", async () => {
    const { importFiles, user } = setup();
    importFiles
      .mockResolvedValueOnce(preview)
      .mockRejectedValueOnce(
        new ApiError(409, "stale_preview", "Data changed"),
      );
    await selectAndValidate(user);
    await user.click(
      screen.getByRole("button", { name: "Подтвердить загрузку" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Подтвердить загрузку" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("проверьте их ещё раз");
    expect(
      screen.getByRole("button", { name: "Проверить данные" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Убрать employees.json" }),
    ).toBeInTheDocument();
  });

  it("requires a new check after a lost commit response without discarding the files", async () => {
    const { importFiles, user } = setup();
    importFiles
      .mockResolvedValueOnce(preview)
      .mockRejectedValueOnce(new Error("Соединение потеряно"));
    await selectAndValidate(user);
    await user.click(
      screen.getByRole("button", { name: "Подтвердить загрузку" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "уже добавленные записи",
    );
    expect(
      screen.getByRole("button", { name: "Подтвердить загрузку" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Проверить повторно" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Убрать employees.json" }),
    ).toBeInTheDocument();
  });

  it("never accepts user files in the demonstration mode", () => {
    const { importFiles } = setup("demo");
    expect(screen.getByLabelText("Выбрать employees.json")).toBeDisabled();
    expect(
      screen.getByLabelText("Выбрать activity_history.csv"),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Проверить данные" }),
    ).toBeDisabled();
    expect(importFiles).not.toHaveBeenCalled();
  });

  it.each(["preview", "commit"] as const)(
    "ignores a late 401 from %s after the import page unmounts",
    async (stage) => {
      const { importFiles, user, unmount, context } = setup();
      const pending = deferred<ImportResponse>();
      if (stage === "commit") {
        importFiles.mockResolvedValueOnce(preview);
        importFiles.mockReturnValueOnce(pending.promise);
        await selectAndValidate(user);
        await user.click(
          screen.getByRole("button", { name: "Подтвердить загрузку" }),
        );
      } else {
        importFiles.mockReturnValueOnce(pending.promise);
        await user.upload(
          screen.getByLabelText("Выбрать employees.json"),
          sourceFile(),
        );
        await user.click(
          screen.getByRole("button", { name: "Проверить данные" }),
        );
      }
      unmount();
      await act(async () => {
        pending.reject(new ApiError(401, "UNAUTHENTICATED", "Old session"));
      });
      expect(context.handleError).not.toHaveBeenCalled();
    },
  );

  it("isolates a new client's pending preview from the old client's late 401", async () => {
    const { importFiles, user, rerender, context, onDone } = setup();
    const oldCommit = deferred<ImportResponse>();
    importFiles
      .mockResolvedValueOnce(preview)
      .mockReturnValueOnce(oldCommit.promise);
    await selectAndValidate(user);
    await user.click(
      screen.getByRole("button", { name: "Подтвердить загрузку" }),
    );

    const nextPreview = deferred<ImportResponse>();
    const nextImport = vi
      .fn<CareerClient["importFiles"]>()
      .mockReturnValue(nextPreview.promise);
    const nextContext: AppContextValue = {
      ...context,
      client: { importFiles: nextImport } as unknown as CareerClient,
      user: { ...context.user, id: "new-hr" },
      handleError: vi.fn(() => "New session error"),
    };
    rerender(
      <AppContext.Provider value={nextContext}>
        <ImportPage onDone={onDone} />
      </AppContext.Provider>,
    );
    expect(
      screen.queryByRole("button", { name: "Убрать employees.json" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Подтвердить загрузку" }),
    ).not.toBeInTheDocument();
    await user.upload(
      screen.getByLabelText("Выбрать employees.json"),
      sourceFile(),
    );
    await user.click(screen.getByRole("button", { name: "Проверить данные" }));

    await act(async () => {
      oldCommit.reject(new ApiError(401, "UNAUTHENTICATED", "Old session"));
    });
    expect(context.handleError).not.toHaveBeenCalled();
    expect(nextContext.handleError).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проверяем…" })).toBeDisabled();
    expect(screen.getByLabelText("Выбрать employees.json")).toBeDisabled();

    await act(async () =>
      nextPreview.resolve({ ...preview, preview_token: "new-session-preview" }),
    );
    expect(
      screen.getByRole("button", { name: "Подтвердить загрузку" }),
    ).toBeEnabled();
    expect(nextImport).toHaveBeenCalledTimes(1);
  });

  it("discards an unfinished file read when the client changes", async () => {
    const { user, context, rerender, onDone } = setup();
    const read = deferred<ArrayBuffer>();
    const file = new File(['{"meta":{},"employees":[]}'], "employees.json", {
      type: "application/json",
    });
    Object.defineProperty(file, "arrayBuffer", { value: () => read.promise });
    await user.upload(screen.getByLabelText("Выбрать employees.json"), file);
    expect(screen.getByLabelText("Выбрать employees.json")).toBeDisabled();

    const nextContext = {
      ...context,
      client: { importFiles: vi.fn() } as unknown as CareerClient,
    };
    rerender(
      <AppContext.Provider value={nextContext}>
        <ImportPage onDone={onDone} />
      </AppContext.Provider>,
    );
    await act(async () =>
      read.resolve(
        new TextEncoder().encode('{"meta":{},"employees":[]}').buffer,
      ),
    );
    expect(
      screen.queryByRole("button", { name: "Убрать employees.json" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Проверить данные" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Выбрать employees.json")).toBeEnabled();
  });
});
