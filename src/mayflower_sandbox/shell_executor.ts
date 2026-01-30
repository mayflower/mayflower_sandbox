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

// Shell command parsing types
interface ParsedCommand {
  argv: string[];
  redirectOut?: string;  // > file
  redirectAppend?: string;  // >> file
  redirectIn?: string;  // < file
}

interface ParsedShell {
  commands: Array<ParsedCommand & { type: "always" | "stop_on_error" | "stop_on_success" }>;
}

// Simple shell command parser - handles basic cases without full shell complexity
function parseShellCommand(command: string): ParsedShell {
  const result: ParsedShell = { commands: [] };

  // Split on && and ; (basic splitting, doesn't handle quotes perfectly)
  const parts = command.split(/\s*(&&|;)\s*/);

  let i = 0;
  while (i < parts.length) {
    const cmdStr = parts[i].trim();
    if (!cmdStr || cmdStr === "&&" || cmdStr === ";") {
      i++;
      continue;
    }

    const type = (i > 0 && parts[i - 1] === "&&") ? "stop_on_error" : "always";
    const parsed = parseSingleCommand(cmdStr);
    result.commands.push({ ...parsed, type });
    i++;
  }

  return result;
}

function parseSingleCommand(cmdStr: string): ParsedCommand {
  const result: ParsedCommand = { argv: [] };

  // Handle output redirection
  let remaining = cmdStr;

  // >> append redirection
  const appendMatch = remaining.match(/\s*>>\s*(\S+)\s*$/);
  if (appendMatch) {
    result.redirectAppend = appendMatch[1];
    remaining = remaining.slice(0, -appendMatch[0].length);
  }

  // > output redirection
  const outMatch = remaining.match(/\s*>\s*(\S+)\s*$/);
  if (outMatch && !result.redirectAppend) {
    result.redirectOut = outMatch[1];
    remaining = remaining.slice(0, -outMatch[0].length);
  }

  // < input redirection
  const inMatch = remaining.match(/\s*<\s*(\S+)\s*/);
  if (inMatch) {
    result.redirectIn = inMatch[1];
    remaining = remaining.replace(inMatch[0], " ");
  }

  // Parse remaining as argv (simple space split, handles basic quoting)
  result.argv = parseArgv(remaining.trim());

  return result;
}

function parseArgv(str: string): string[] {
  const args: string[] = [];
  let current = "";
  let inQuote: string | null = null;

  for (let i = 0; i < str.length; i++) {
    const c = str[i];

    if (inQuote) {
      if (c === inQuote) {
        inQuote = null;
      } else {
        current += c;
      }
    } else if (c === '"' || c === "'") {
      inQuote = c;
    } else if (c === " " || c === "\t") {
      if (current) {
        args.push(current);
        current = "";
      }
    } else {
      current += c;
    }
  }

  if (current) {
    args.push(current);
  }

  return args;
}

function executeApplet(
  module: BusyboxModule,
  cmd: ParsedCommand,
  output: OutputCapture,
): number {
  if (cmd.argv.length === 0) {
    return 0;
  }

  let exitCode = 0;

  // Set up redirection if needed
  const hasRedirect = !!(cmd.redirectOut || cmd.redirectAppend);
  if (hasRedirect) {
    output.currentRedirect = cmd.redirectOut || cmd.redirectAppend;
    output.redirectBuffer = [];
  }

  const originalQuit = module.quit;
  module.quit = (status: number, toThrow?: unknown) => {
    exitCode = status;
    if (toThrow) {
      throw toThrow;
    }
    throw new Error("ExitStatus");
  };

  try {
    // Call applet directly: busybox <applet> <args...>
    module.callMain(["busybox", ...cmd.argv]);
  } catch (err) {
    const name = (err && typeof err === "object") ? (err as { name?: string }).name : undefined;
    const message = (err && typeof err === "object") ? (err as { message?: string }).message : undefined;
    if (name != "ExitStatus" && message != "ExitStatus") {
      throw err;
    }
  } finally {
    module.quit = originalQuit;
  }

  // Handle redirections - write buffered output to file
  if (hasRedirect && output.redirectBuffer) {
    const content = output.redirectBuffer.join("\n") + (output.redirectBuffer.length ? "\n" : "");
    const path = cmd.redirectOut || cmd.redirectAppend!;

    try {
      // Ensure parent directory exists
      const dir = path.substring(0, path.lastIndexOf("/"));
      if (dir && dir !== "/") {
        try {
          module.FS.mkdirTree(dir);
        } catch {
          // Directory may already exist
        }
      }

      if (cmd.redirectAppend) {
        try {
          const existing = module.FS.readFile(path, { encoding: "utf8" }) as string;
          module.FS.writeFile(path, existing + content);
        } catch {
          module.FS.writeFile(path, content);
        }
      } else {
        module.FS.writeFile(path, content);
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      output.stderr.push(`Cannot redirect to ${path}: ${errMsg}`);
      exitCode = 1;
    }

    // Clear redirect state
    output.currentRedirect = undefined;
    output.redirectBuffer = undefined;
  }

  return exitCode;
}

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

interface OutputCapture {
  stdout: string[];
  stderr: string[];
  // For handling per-command redirection
  currentRedirect?: string;
  redirectBuffer?: string[];
}

async function loadBusyboxModule(dir: string, output: OutputCapture): Promise<BusyboxModule> {
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
    // Set thisProgram to "busybox" so applets can be invoked
    thisProgram: "busybox",
    // Capture stdout/stderr during module initialization
    // Check if we're in redirect mode and buffer accordingly
    print: (text: string) => {
      if (output.currentRedirect) {
        output.redirectBuffer?.push(text);
      } else {
        output.stdout.push(text);
      }
    },
    printErr: (text: string) => output.stderr.push(text),
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

function runCommand(
  module: BusyboxModule,
  command: string,
  output: OutputCapture,
): { stdout: string; stderr: string; exit_code: number } {
  let exitCode = 0;

  module.stdin = () => null;

  const originalQuit = module.quit;
  module.quit = (status: number, toThrow?: unknown) => {
    exitCode = status;
    if (toThrow) {
      throw toThrow;
    }
    throw new Error("ExitStatus");
  };

  try {
    // Parse command and execute applets directly to avoid vfork
    const parsed = parseShellCommand(command);
    for (const cmd of parsed.commands) {
      if (cmd.type === "stop_on_error" && exitCode !== 0) {
        break;
      }
      exitCode = executeApplet(module, cmd, output);
    }
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
    stdout: output.stdout.join("\n"),
    stderr: output.stderr.join("\n"),
    exit_code: exitCode,
  };
}

async function executeShell(options: ShellExecutionOptions): Promise<ShellExecutionResult> {
  const start = Date.now();
  const output: OutputCapture = { stdout: [], stderr: [] };

  try {
    const busyboxDir = await resolveBusyboxDir(options.busyboxDir);
    const module = await loadBusyboxModule(busyboxDir, output);
    const fs = module.FS;

    ensureBaseDirs(fs);
    if (options.files) {
      await mountFiles(fs, options.files);
    }

    const before = snapshotFs(fs, "/");
    const { stdout, stderr, exit_code } = runCommand(module, options.command, output);
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
