/**
 * Mayflower Sandbox - Busybox Shell Executor (WASM + VFS)
 *
 * This script loads VFS files from stdin, materializes them in BusyBox MEMFS,
 * executes the command via `sh -c`, and returns changed files back to Python.
 */

import { parseArgs } from "jsr:@std/cli@1.0.23/parse-args";
import { fromFileUrl, join, resolve, toFileUrl } from "@std/path";

interface ShellExecutionOptions {
  command: string;
  files?: Record<string, Uint8Array>;
  busyboxDir?: string;
}

interface ShellExecutionResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exit_code: number;
  execution_time_ms: number;
  created_files?: Array<{ path: string; content: number[] }>;
}

type BusyboxModule = {
  FS: any;
  callMain: (args: string[]) => void;
  quit?: (status: number, toThrow?: unknown) => void;
  print?: (text: string) => void;
  printErr?: (text: string) => void;
  stdin?: () => number | null;
  thisProgram?: string;
  noExitRuntime?: boolean;
  noInitialRun?: boolean;
};

type BusyboxModuleFactory = (options: Record<string, unknown>) => Promise<BusyboxModule>;

const IGNORE_PREFIXES = ["/dev", "/proc", "/sys"];

async function readStdinFiles(): Promise<Record<string, Uint8Array>> {
  const files: Record<string, Uint8Array> = {};
  const chunks: Uint8Array[] = [];
  const buffer = new Uint8Array(8192);

  while (true) {
    const bytesRead = await Deno.stdin.read(buffer);
    if (bytesRead === null) break;
    chunks.push(buffer.slice(0, bytesRead));
  }

  const stdinData = new Uint8Array(chunks.reduce((acc, chunk) => acc + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) {
    stdinData.set(chunk, offset);
    offset += chunk.length;
  }

  if (stdinData.length === 0) {
    return files;
  }

  const magic = new TextDecoder().decode(stdinData.slice(0, 4));
  if (!magic.startsWith("MFS")) {
    return files;
  }

  const metadataLength = new DataView(stdinData.buffer).getUint32(4, false);
  const metadataBytes = stdinData.slice(8, 8 + metadataLength);
  const metadata = JSON.parse(new TextDecoder().decode(metadataBytes));

  let fileOffset = 8 + metadataLength;
  for (const file of metadata.files || []) {
    const content = stdinData.slice(fileOffset, fileOffset + file.size);
    files[file.path] = content;
    fileOffset += file.size;
  }

  return files;
}

function ensureDir(fs: any, path: string): void {
  const parts = path.split("/").filter(Boolean);
  let current = "";
  for (const part of parts.slice(0, -1)) {
    current += `/${part}`;
    try {
      fs.mkdir(current);
    } catch (err) {
      const info = err as { code?: string; errno?: string } | undefined;
      const code = info?.code ?? info?.errno ?? "";
      if (code && code !== "EEXIST") {
        throw err;
      }
    }
  }
}

function ensureBaseDirs(fs: any): void {
  for (const dir of ["/tmp", "/home", "/root", "/bin"]) {
    try {
      fs.mkdir(dir);
    } catch {
      // Ignore if already exists
    }
  }
}

function normalizePath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

async function mountFiles(fs: any, files: Record<string, Uint8Array>): Promise<void> {
  for (const [path, content] of Object.entries(files)) {
    const normalized = normalizePath(path);
    ensureDir(fs, normalized);
    fs.writeFile(normalized, content, { encoding: "binary" });
  }
}

function statStamp(stat: any): number {
  if (typeof stat.mtime === "number") {
    return stat.mtime;
  }
  if (stat.mtime instanceof Date) {
    return stat.mtime.getTime();
  }
  return stat.size ?? 0;
}

function shouldIgnorePath(path: string): boolean {
  return IGNORE_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

function snapshotFs(fs: any, root: string): Map<string, number> {
  const snapshot = new Map<string, number>();
  const stack: string[] = [root];

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) continue;
    if (shouldIgnorePath(current)) continue;

    let entries: string[] = [];
    try {
      entries = fs.readdir(current) as string[];
    } catch {
      continue;
    }

    for (const entry of entries) {
      if (entry === "." || entry === "..") continue;
      const entryPath = current === "/" ? `/${entry}` : `${current}/${entry}`;
      if (shouldIgnorePath(entryPath)) continue;

      try {
        const stat = fs.stat(entryPath);
        if (fs.isDir(stat.mode)) {
          stack.push(entryPath);
        } else if (fs.isFile(stat.mode)) {
          snapshot.set(entryPath, statStamp(stat));
        }
      } catch {
        // Ignore entries that disappear
      }
    }
  }

  return snapshot;
}

function findChangedFiles(before: Map<string, number>, after: Map<string, number>): string[] {
  const changed: string[] = [];
  for (const [path, stamp] of after.entries()) {
    if (!before.has(path) || before.get(path) !== stamp) {
      changed.push(path);
    }
  }
  return changed;
}

function collectFiles(fs: any, paths: string[]): Array<{ path: string; content: number[] }> {
  const collected: Array<{ path: string; content: number[] }> = [];
  for (const path of paths) {
    try {
      const content = fs.readFile(path, { encoding: "binary" }) as Uint8Array;
      collected.push({ path, content: Array.from(content) });
    } catch {
      // Ignore unreadable files
    }
  }
  return collected;
}

async function resolveBusyboxDir(explicitDir?: string): Promise<string> {
  const scriptDir = fromFileUrl(new URL(".", import.meta.url));
  const candidates: string[] = [];

  if (explicitDir) {
    candidates.push(explicitDir);
  }

  candidates.push(scriptDir);
  candidates.push(join(scriptDir, "busybox"));
  candidates.push(join(scriptDir, "..", "busybox"));
  candidates.push(resolve(scriptDir, "../../../..", "busybox-wasm", "release"));
  candidates.push(resolve(scriptDir, "../../../..", "busybox-wasm", "build", "wasm"));

  for (const dir of candidates) {
    try {
      await Deno.stat(join(dir, "busybox.js"));
      await Deno.stat(join(dir, "busybox.wasm"));
      return dir;
    } catch {
      // try next candidate
    }
  }

  throw new Error(
    `BusyBox assets not found. Searched: ${candidates.join(", ")}`,
  );
}

async function loadBusyboxModule(dir: string): Promise<BusyboxModule> {
  const moduleUrl = toFileUrl(join(dir, "busybox.js"));
  const wasmPath = join(dir, "busybox.wasm");
  const mod = await import(moduleUrl.href);
  if (!mod?.default) {
    throw new Error("busybox.js did not export a default module factory");
  }

  const factory = mod.default as BusyboxModuleFactory;
  const module = await factory({
    noInitialRun: true,
    noExitRuntime: true,
    locateFile(path: string) {
      if (path.endsWith(".wasm")) {
        return wasmPath;
      }
      return path;
    },
    async instantiateWasm(
      imports: WebAssembly.Imports,
      successCallback: (instance: WebAssembly.Instance) => void,
    ) {
      const wasmBinary = await Deno.readFile(wasmPath);
      const { instance } = await WebAssembly.instantiate(wasmBinary, imports);
      successCallback(instance);
      return instance.exports;
    },
  });

  return module;
}

function runCommand(module: BusyboxModule, command: string): { stdout: string; stderr: string; exit_code: number } {
  const stdout: string[] = [];
  const stderr: string[] = [];
  let exitCode = 0;

  module.print = (text: string) => stdout.push(text);
  module.printErr = (text: string) => stderr.push(text);
  module.stdin = () => null;
  module.thisProgram = "busybox";

  const originalQuit = module.quit;
  module.quit = (status: number, toThrow?: unknown) => {
    exitCode = status;
    if (toThrow) {
      throw toThrow;
    }
    throw new Error("ExitStatus");
  };

  try {
    module.callMain(["sh", "-c", command]);
  } catch (err) {
    const name = (err && typeof err === "object") ? (err as { name?: string }).name : undefined;
    const message = (err && typeof err === "object") ? (err as { message?: string }).message : undefined;
    if (name != "ExitStatus" && message != "ExitStatus") {
      throw err;
    }
  } finally {
    module.quit = originalQuit;
  }

  return {
    stdout: stdout.join("\n"),
    stderr: stderr.join("\n"),
    exit_code: exitCode,
  };
}

async function executeShell(options: ShellExecutionOptions): Promise<ShellExecutionResult> {
  const start = Date.now();

  try {
    const busyboxDir = await resolveBusyboxDir(options.busyboxDir);
    const module = await loadBusyboxModule(busyboxDir);
    const fs = module.FS;

    ensureBaseDirs(fs);
    if (options.files) {
      await mountFiles(fs, options.files);
    }

    const before = snapshotFs(fs, "/");
    const { stdout, stderr, exit_code } = runCommand(module, options.command);
    const after = snapshotFs(fs, "/");

    const changedPaths = findChangedFiles(before, after);
    const createdFiles = changedPaths.length > 0 ? collectFiles(fs, changedPaths) : undefined;

    return {
      success: exit_code === 0,
      stdout,
      stderr,
      exit_code,
      execution_time_ms: Date.now() - start,
      created_files: createdFiles,
    };
  } catch (err) {
    return {
      success: false,
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exit_code: 1,
      execution_time_ms: Date.now() - start,
    };
  }
}

async function main() {
  const args = parseArgs(Deno.args, {
    string: ["command", "busybox-dir"],
    alias: { c: "command", b: "busybox-dir" },
  });

  if (!args.command) {
    console.error("Usage: shell_executor.ts --command '<cmd>' [--busybox-dir <path>]");
    Deno.exit(1);
  }

  const files = await readStdinFiles();
  const result = await executeShell({
    command: args.command,
    files,
    busyboxDir: args["busybox-dir"],
  });

  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
