/** Start the local UI and existing backend; --demo uses an isolated synthetic DB. */
import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const frontend = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const root = path.dirname(frontend);
const children = new Set();
const controller = new AbortController();
let stopping = false;
let shutdownPromise;

function port(name, fallback) {
  const value = process.env[name] ?? String(fallback);
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error(`${name} must be an integer between 1 and 65535.`);
  }
  return Number(value);
}

async function exists(filename) {
  try {
    await access(filename);
    return true;
  } catch {
    return false;
  }
}

async function requireFreePort(value) {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", () =>
      reject(
        new Error(
          `Port ${value} is unavailable; existing processes were left running.`,
        ),
      ),
    );
    probe.listen({ host: "127.0.0.1", port: value, exclusive: true }, () =>
      probe.close(resolve),
    );
  });
}

function launch(name, executable, args, env, persistent = true, cwd = root) {
  if (stopping) throw new Error("Startup was cancelled.");
  const child = spawn(executable, args, {
    cwd,
    env,
    stdio: ["ignore", "inherit", "inherit"],
    shell: false,
    windowsHide: true,
    detached: process.platform !== "win32",
  });
  children.add(child);
  child.once("error", () => {
    if (!stopping) {
      console.error(
        `${name} could not start. Check the interpreter and installed dependencies.`,
      );
      void shutdown(1);
    }
  });
  child.once("exit", (code, signal) => {
    children.delete(child);
    if (persistent && !stopping) {
      console.error(
        `${name} stopped (${signal ?? `exit ${code}`}); stopping this launch.`,
      );
      void shutdown(code && code > 0 ? code : 1);
    }
  });
  return child;
}

function exited(child) {
  return child.exitCode !== null || child.signalCode !== null;
}

async function stopChild(child) {
  if (!child.pid || exited(child)) return;
  if (process.platform === "win32") {
    // Only this runner's child PID and descendants. Never select processes by name or port.
    const taskkill = path.join(
      process.env.SystemRoot ?? "C:\\Windows",
      "System32",
      "taskkill.exe",
    );
    await new Promise((resolve) => {
      const killer = spawn(taskkill, ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "ignore",
        shell: false,
        windowsHide: true,
      });
      killer.once("error", () => {
        child.kill();
        resolve();
      });
      killer.once("exit", resolve);
    });
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch (error) {
      if (error.code !== "ESRCH") child.kill("SIGTERM");
    }
    await Promise.race([
      new Promise((resolve) => child.once("exit", resolve)),
      delay(3000),
    ]);
    if (!exited(child)) {
      try {
        process.kill(-child.pid, "SIGKILL");
      } catch {
        /* Already stopped. */
      }
    }
  }
}

function shutdown(code) {
  if (shutdownPromise) return shutdownPromise;
  stopping = true;
  controller.abort();
  shutdownPromise = (async () => {
    await Promise.allSettled([...children].map(stopChild));
    process.exit(code);
  })();
  return shutdownPromise;
}

process.once("SIGINT", () => {
  void shutdown(0);
});
process.once("SIGTERM", () => {
  void shutdown(0);
});

async function waitReady(child, origin) {
  const deadline = Date.now() + 30000;
  while (!stopping && Date.now() < deadline) {
    if (exited(child))
      throw new Error("Backend stopped before becoming ready.");
    try {
      const response = await fetch(`${origin}/api/health/ready`, {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(1500)]),
      });
      if (response.ok && (await response.json()).status === "ready") return;
    } catch {
      /* The process may still be opening its database. */
    }
    await delay(200, undefined, { signal: controller.signal });
  }
  throw new Error("Backend readiness did not succeed within 30 seconds.");
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes("--help")) {
    console.log("Usage: node scripts/start.mjs [--demo]");
    console.log(
      "Requires frontend dependencies and the root Python .venv (or CQ_PYTHON).",
    );
    console.log(
      "Optional CQ_API_PORT / CQ_UI_PORT override 8000 / 5173. Normal mode preserves DATABASE_PATH.",
    );
    return;
  }
  if (args.some((arg) => arg !== "--demo"))
    throw new Error("Only --demo and --help are supported.");
  const demo = args.includes("--demo");
  const apiPort = port("CQ_API_PORT", 8000);
  const uiPort = port("CQ_UI_PORT", 5173);
  if (apiPort === uiPort)
    throw new Error("Backend and frontend ports must differ.");
  await Promise.all([requireFreePort(apiPort), requireFreePort(uiPort)]);

  const python =
    process.env.CQ_PYTHON ||
    path.join(
      root,
      ".venv",
      process.platform === "win32" ? "Scripts" : "bin",
      process.platform === "win32" ? "python.exe" : "python",
    );
  if (!process.env.CQ_PYTHON && !(await exists(python))) {
    throw new Error(
      "Create the root Python .venv and install requirements-dev.lock, or set CQ_PYTHON.",
    );
  }
  const vite = path.join(frontend, "node_modules", "vite", "bin", "vite.js");
  if (!(await exists(vite)))
    throw new Error(
      "Install frontend dependencies before starting the application.",
    );

  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  const uiOrigin = `http://127.0.0.1:${uiPort}`;
  let existingOrigins = [];
  if (process.env.ALLOWED_ORIGINS) {
    try {
      existingOrigins = JSON.parse(process.env.ALLOWED_ORIGINS);
    } catch {
      throw new Error("ALLOWED_ORIGINS must be a JSON array of exact origins.");
    }
    if (
      !Array.isArray(existingOrigins) ||
      existingOrigins.some((item) => typeof item !== "string")
    ) {
      throw new Error("ALLOWED_ORIGINS must be a JSON array of exact origins.");
    }
  }
  const env = {
    ...process.env,
    ALLOWED_ORIGINS: JSON.stringify([
      ...new Set([
        ...existingOrigins,
        uiOrigin,
        `http://localhost:${uiPort}`,
        apiOrigin,
      ]),
    ]),
  };
  if (demo) {
    const database = path.join(frontend, ".qa", "demo.sqlite3");
    const credentials = path.join(frontend, ".local-demo.json");
    env.DATABASE_PATH = database;
    env.APP_ENV = "development";
    env.COOKIE_SECURE = "false";
    env.AI_ENABLED = "false";
    const seeder = launch(
      "Synthetic demo setup",
      python,
      [
        path.join(frontend, "scripts", "seed-demo.py"),
        "--database",
        database,
        "--credentials",
        credentials,
      ],
      env,
      false,
    );
    const code = await new Promise((resolve, reject) => {
      seeder.once("error", reject);
      seeder.once("exit", resolve);
    });
    if (code !== 0)
      throw new Error("Demo setup failed; no existing database was reset.");
    console.log(
      `Synthetic mode; AI is disabled. Local credentials: ${credentials}`,
    );
  }

  const backendArgs = [
    "-m",
    "uvicorn",
    "backend.app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    String(apiPort),
    "--no-access-log",
  ];
  if (await exists(path.join(root, ".env")))
    backendArgs.push("--env-file", path.join(root, ".env"));
  const backend = launch("Backend", python, backendArgs, env);
  await waitReady(backend, apiOrigin);
  console.log(`Backend ready: ${apiOrigin}`);
  launch(
    "Frontend",
    process.execPath,
    [vite, "--host", "127.0.0.1", "--port", String(uiPort), "--strictPort"],
    { ...env, API_PROXY_TARGET: apiOrigin },
    true,
    frontend,
  );
  console.log(`Career Quest: ${uiOrigin} — press Ctrl+C to stop this launch.`);
}

main().catch((error) => {
  if (!stopping) console.error(error.message);
  void shutdown(1);
});
