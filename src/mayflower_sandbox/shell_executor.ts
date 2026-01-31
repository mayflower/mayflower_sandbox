/**
 * Mayflower Sandbox - Busybox Shell Executor (WASM + VFS)
 *
 * This script loads VFS files from stdin, materializes them in BusyBox MEMFS,
 * executes the command via `sh -c`, and returns changed files back to Python.
 *
 * Supports two execution modes:
 * 1. Simple mode: Direct applet invocation for commands without pipes/variables
 * 2. Full shell mode: ProcessManager-based execution for complex shell features
 */

import { parseArgs } from "jsr:@std/cli@1.0.23/parse-args";
import { fromFileUrl, join, resolve, toFileUrl } from "@std/path";
import { ProcessManager } from "./shell_process_manager.ts";

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
  callMain: (args: string[]) => number;  // Returns exit code
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

/**
 * Check if a command contains pipes (|) that need Worker-based execution.
 */
function hasPipes(command: string): boolean {
  // Match | but not || (which is OR)
  // Simple approach: look for | not preceded/followed by |
  return /(?<!\|)\|(?!\|)/.test(command);
}

/**
 * Check if a command requires full shell support (ProcessManager).
 * Returns true for commands with variables, subshells, etc.
 * Note: Pipes are now handled separately via Worker-based execution.
 */
function needsFullShell(command: string): boolean {
  // Patterns that require full shell support (excluding pipes which we handle via Workers)
  const complexPatterns = [
    /\|\|/,         // Or chains
    /\$\w/,         // Variable expansion
    /\$\(/,         // Command substitution
    /`/,            // Backtick substitution
    /\bfor\b/,      // For loops
    /\bwhile\b/,    // While loops
    /\bif\b/,       // If statements
    /\bcase\b/,     // Case statements
    /<<</,          // Here strings
    /<</,           // Here documents
    /\(\s*\)/,      // Subshells
    /\{.*\}/,       // Brace expansion (basic check)
    /\bexport\b/,   // Export (modifies env)
    /\beval\b/,     // Eval
    /\bsource\b/,   // Source/dot commands
    /^\s*\./,       // Dot command
  ];

  return complexPatterns.some((pattern) => pattern.test(command));
}

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

/**
 * Simple shell command parser - handles basic cases without full shell complexity.
 *
 * Limitations:
 * - Does not support pipes (|)
 * - Does not support || (or-chaining)
 * - Does not support subshells ($(...) or backticks)
 * - Does not support variable expansion ($VAR)
 * - Does not support here-documents (<<EOF)
 * - Quoted delimiters (e.g., "&&") may not be handled correctly
 * - Escape sequences within quotes are not processed
 */
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

const APPEND_REDIRECT_RE = /\s*>>\s*(\S+)\s*$/;
const OUTPUT_REDIRECT_RE = /\s*>\s*(\S+)\s*$/;
const INPUT_REDIRECT_RE = /\s*<\s*(\S+)\s*/;

function parseSingleCommand(cmdStr: string): ParsedCommand {
  const result: ParsedCommand = { argv: [] };
  let remaining = cmdStr;

  // >> append redirection
  const appendMatch = APPEND_REDIRECT_RE.exec(remaining);
  if (appendMatch) {
    result.redirectAppend = appendMatch[1];
    remaining = remaining.slice(0, -appendMatch[0].length);
  }

  // > output redirection (only if no append)
  if (!result.redirectAppend) {
    const outMatch = OUTPUT_REDIRECT_RE.exec(remaining);
    if (outMatch) {
      result.redirectOut = outMatch[1];
      remaining = remaining.slice(0, -outMatch[0].length);
    }
  }

  // < input redirection
  const inMatch = INPUT_REDIRECT_RE.exec(remaining);
  if (inMatch) {
    result.redirectIn = inMatch[1];
    remaining = remaining.replace(inMatch[0], " ");
  }

  result.argv = parseArgv(remaining.trim());
  return result;
}

function parseArgv(str: string): string[] {
  const args: string[] = [];
  let current = "";
  let inQuote: string | null = null;

  for (const c of str) {
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

function isExitStatusError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const name = (err as { name?: string }).name;
  const message = (err as { message?: string }).message;
  return name === "ExitStatus" || message === "ExitStatus";
}

// Emscripten errno values
const ENOENT = 44;  // No such file or directory
const EEXIST = 20;  // File exists (actually ENOTDIR in some contexts)
const ENOTDIR = 54; // Not a directory

function getErrorCode(err: unknown): string {
  const info = err as { code?: string; errno?: string | number; name?: string } | undefined;
  if (info?.code) return info.code;
  if (info?.name === "ErrnoError") {
    // Emscripten ErrnoError - convert numeric errno to string
    const errno = info.errno;
    if (errno === ENOENT || errno === 44) return "ENOENT";
    if (errno === EEXIST || errno === 17) return "EEXIST";
    if (errno === ENOTDIR || errno === 20) return "ENOTDIR";
    return `ERRNO_${errno}`;
  }
  if (typeof info?.errno === "string") return info.errno;
  return "";
}

function ensureParentDir(fs: any, path: string): void {
  const dir = path.substring(0, path.lastIndexOf("/"));
  if (dir && dir !== "/") {
    try {
      fs.mkdirTree(dir);
    } catch (err) {
      const code = getErrorCode(err);
      // Ignore EEXIST and ENOTDIR (Emscripten errno quirks)
      if (code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")) {
        throw err;
      }
    }
  }
}

function writeRedirectContent(fs: any, path: string, content: string, append: boolean): void {
  if (append) {
    try {
      const existing = fs.readFile(path, { encoding: "utf8" }) as string;
      fs.writeFile(path, existing + content);
    } catch (err) {
      const code = getErrorCode(err);
      if (code === "ENOENT") {
        // File doesn't exist yet, create it
        fs.writeFile(path, content);
      } else {
        throw err;
      }
    }
  } else {
    fs.writeFile(path, content);
  }
}

function handleRedirection(
  module: BusyboxModule,
  cmd: ParsedCommand,
  output: OutputCapture,
): number {
  if (!output.redirectBuffer) return 0;

  const content = output.redirectBuffer.join("\n") + (output.redirectBuffer.length ? "\n" : "");
  const path = cmd.redirectOut || cmd.redirectAppend!;

  try {
    ensureParentDir(module.FS, path);
    writeRedirectContent(module.FS, path, content, !!cmd.redirectAppend);
    return 0;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    output.stderr.push(`Cannot redirect to ${path}: ${errMsg}`);
    return 1;
  } finally {
    output.currentRedirect = undefined;
    output.redirectBuffer = undefined;
  }
}

function executeApplet(
  module: BusyboxModule,
  cmd: ParsedCommand,
  output: OutputCapture,
): number {
  if (cmd.argv.length === 0) return 0;

  let exitCode = 0;
  const hasRedirect = !!(cmd.redirectOut || cmd.redirectAppend);

  if (hasRedirect) {
    output.currentRedirect = cmd.redirectOut || cmd.redirectAppend;
    output.redirectBuffer = [];
  }

  const originalQuit = module.quit;
  module.quit = (status: number, toThrow?: unknown) => {
    exitCode = status;
    if (toThrow) throw toThrow;
    throw new Error("ExitStatus");
  };

  try {
    // callMain returns the exit code directly
    const result = module.callMain(["busybox", ...cmd.argv]);
    if (typeof result === "number") {
      exitCode = result;
    }
  } catch (err) {
    if (!isExitStatusError(err)) throw err;
  } finally {
    module.quit = originalQuit;
  }

  if (hasRedirect) {
    const redirectResult = handleRedirection(module, cmd, output);
    if (redirectResult !== 0) exitCode = redirectResult;
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
      const code = getErrorCode(err);
      // Ignore EEXIST and ENOTDIR (Emscripten errno quirks)
      if (code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")) {
        throw err;
      }
    }
  }
}

function ensureBaseDirs(fs: any): void {
  for (const dir of ["/tmp", "/home", "/root", "/bin"]) {
    try {
      fs.mkdir(dir);
    } catch (err) {
      const code = getErrorCode(err);
      // Ignore EEXIST and ENOTDIR (Emscripten sometimes returns ENOTDIR for existing dirs)
      if (code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")) {
        throw err;
      }
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

function isSpecialEntry(entry: string): boolean {
  return entry === "." || entry === "..";
}

function buildEntryPath(current: string, entry: string): string {
  return current === "/" ? `/${entry}` : `${current}/${entry}`;
}

function tryReadDir(fs: any, path: string): string[] {
  try {
    return fs.readdir(path) as string[];
  } catch (err) {
    const code = getErrorCode(err);
    // Return empty for expected cases: path doesn't exist or isn't a directory
    if (code === "ENOENT" || code === "ENOTDIR") {
      return [];
    }
    throw err;
  }
}

function processEntry(
  fs: any,
  entryPath: string,
  snapshot: Map<string, number>,
  stack: string[],
): void {
  try {
    const stat = fs.stat(entryPath);
    if (fs.isDir(stat.mode)) {
      stack.push(entryPath);
    } else if (fs.isFile(stat.mode)) {
      snapshot.set(entryPath, statStamp(stat));
    }
  } catch (err) {
    const code = getErrorCode(err);
    // Only ignore entries that were deleted between readdir and stat
    if (code !== "ENOENT") {
      throw err;
    }
  }
}

function snapshotFs(fs: any, root: string): Map<string, number> {
  const snapshot = new Map<string, number>();
  const stack: string[] = [root];

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current || shouldIgnorePath(current)) continue;

    const entries = tryReadDir(fs, current);
    for (const entry of entries) {
      if (isSpecialEntry(entry)) continue;
      const entryPath = buildEntryPath(current, entry);
      if (shouldIgnorePath(entryPath)) continue;
      processEntry(fs, entryPath, snapshot, stack);
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
    } catch (err) {
      const code = getErrorCode(err);
      // File may have been deleted between snapshot and collect - that's expected
      if (code !== "ENOENT") {
        console.error(`Warning: Could not read file ${path}: ${err instanceof Error ? err.message : String(err)}`);
      }
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

// ============================================================================
// Worker-based Pipeline Execution
// ============================================================================

const PIPE_HEADER_SIZE = 16;
const PIPE_BUFFER_SIZE = 8192;

interface PipeBuffer {
  buffer: SharedArrayBuffer;
  control: Int32Array;
}

function createPipeBuffer(): PipeBuffer {
  const buffer = new SharedArrayBuffer(PIPE_HEADER_SIZE + PIPE_BUFFER_SIZE);
  const control = new Int32Array(buffer, 0, 4);
  Atomics.store(control, 0, 0);  // readPtr
  Atomics.store(control, 1, 0);  // writePtr
  Atomics.store(control, 2, 0);  // closed
  return { buffer, control };
}

function readAllFromPipeBuffer(pipe: PipeBuffer): string {
  const data = new Uint8Array(pipe.buffer, PIPE_HEADER_SIZE);
  const chunks: Uint8Array[] = [];

  while (true) {
    const readPtr = Atomics.load(pipe.control, 0);
    const writePtr = Atomics.load(pipe.control, 1);
    const closed = Atomics.load(pipe.control, 2);

    const available = (writePtr - readPtr + PIPE_BUFFER_SIZE) % PIPE_BUFFER_SIZE;

    if (available === 0) {
      if (closed !== 0) break;
      Atomics.wait(pipe.control, 1, writePtr, 100);
      continue;
    }

    const chunk = new Uint8Array(available);
    for (let i = 0; i < available; i++) {
      chunk[i] = data[(readPtr + i) % PIPE_BUFFER_SIZE];
    }
    chunks.push(chunk);

    Atomics.store(pipe.control, 0, (readPtr + available) % PIPE_BUFFER_SIZE);
    Atomics.notify(pipe.control, 0, 1);
  }

  const totalLen = chunks.reduce((sum, c) => sum + c.length, 0);
  const result = new Uint8Array(totalLen);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }

  return new TextDecoder().decode(result);
}

// Worker code for running BusyBox commands with pipe I/O
const BUSYBOX_WORKER_CODE = `
const PIPE_HEADER_SIZE = 16;
const PIPE_BUFFER_SIZE = 8192;

class PipeWriter {
  constructor(buffer) {
    this.control = new Int32Array(buffer, 0, 4);
    this.data = new Uint8Array(buffer, PIPE_HEADER_SIZE);
  }
  write(text) {
    const src = new TextEncoder().encode(text);
    let written = 0;
    while (written < src.length) {
      const readPtr = Atomics.load(this.control, 0);
      const writePtr = Atomics.load(this.control, 1);
      let available = (readPtr - writePtr - 1 + PIPE_BUFFER_SIZE) % PIPE_BUFFER_SIZE;
      if (available === 0) available = PIPE_BUFFER_SIZE - 1;
      if (available === 0) {
        Atomics.wait(this.control, 0, readPtr, 100);
        continue;
      }
      const toWrite = Math.min(src.length - written, available);
      for (let i = 0; i < toWrite; i++) {
        this.data[(writePtr + i) % PIPE_BUFFER_SIZE] = src[written + i];
      }
      Atomics.store(this.control, 1, (writePtr + toWrite) % PIPE_BUFFER_SIZE);
      Atomics.notify(this.control, 1, 1);
      written += toWrite;
    }
  }
  close() {
    Atomics.store(this.control, 2, 1);
    Atomics.notify(this.control, 1, 1);
  }
}

class PipeReader {
  constructor(buffer) {
    this.control = new Int32Array(buffer, 0, 4);
    this.data = new Uint8Array(buffer, PIPE_HEADER_SIZE);
  }
  read(maxLen) {
    while (true) {
      const readPtr = Atomics.load(this.control, 0);
      const writePtr = Atomics.load(this.control, 1);
      const closed = Atomics.load(this.control, 2);
      const available = (writePtr - readPtr + PIPE_BUFFER_SIZE) % PIPE_BUFFER_SIZE;
      if (available === 0) {
        if (closed !== 0) return null;
        Atomics.wait(this.control, 1, writePtr, 100);
        continue;
      }
      const toRead = Math.min(maxLen, available);
      const result = new Uint8Array(toRead);
      for (let i = 0; i < toRead; i++) {
        result[i] = this.data[(readPtr + i) % PIPE_BUFFER_SIZE];
      }
      Atomics.store(this.control, 0, (readPtr + toRead) % PIPE_BUFFER_SIZE);
      Atomics.notify(this.control, 0, 1);
      return result;
    }
  }
  readAll() {
    const chunks = [];
    while (true) {
      const chunk = this.read(4096);
      if (chunk === null) break;
      chunks.push(chunk);
    }
    const totalLen = chunks.reduce((sum, c) => sum + c.length, 0);
    const result = new Uint8Array(totalLen);
    let offset = 0;
    for (const chunk of chunks) {
      result.set(chunk, offset);
      offset += chunk.length;
    }
    return new TextDecoder().decode(result);
  }
}

let stdoutPipe = null;
let stdinPipe = null;
let module = null;
let vfsFiles = {};
const outputBuffer = [];
const stderrBuffer = [];

self.onmessage = async (e) => {
  const msg = e.data;

  if (msg.type === "init") {
    try {
      if (msg.stdoutBuffer) stdoutPipe = new PipeWriter(msg.stdoutBuffer);
      if (msg.stdinBuffer) stdinPipe = new PipeReader(msg.stdinBuffer);
      if (msg.files) vfsFiles = msg.files;

      const factory = (await import(msg.jsPath)).default;
      module = await factory({
        noInitialRun: true,
        noExitRuntime: true,
        thisProgram: "busybox",
        print: (text) => {
          if (stdoutPipe) stdoutPipe.write(text + "\\n");
          else outputBuffer.push(text);
        },
        printErr: (text) => stderrBuffer.push(text),
        locateFile(path) {
          if (path.endsWith(".wasm")) return msg.wasmPath;
          return path;
        },
        stdin: stdinPipe ? () => {
          const chunk = stdinPipe.read(1);
          if (chunk === null) return null;
          return chunk[0];
        } : undefined,
      });

      // Mount VFS files into MEMFS
      const FS = module.FS;
      for (const [filePath, content] of Object.entries(vfsFiles)) {
        const dir = filePath.substring(0, filePath.lastIndexOf("/"));
        if (dir && dir !== "/") {
          try {
            FS.mkdirTree(dir);
          } catch (e) {}
        }
        try {
          const data = typeof content === "string"
            ? new TextEncoder().encode(content)
            : new Uint8Array(content);
          FS.writeFile(filePath, data);
        } catch (e) {}
      }

      self.postMessage({ type: "ready" });
    } catch (err) {
      self.postMessage({ type: "error", error: String(err) });
    }
  } else if (msg.type === "run") {
    try {
      const exitCode = module.callMain(msg.argv);
      if (stdoutPipe) stdoutPipe.close();
      self.postMessage({
        type: "done",
        exitCode,
        output: outputBuffer.join("\\n"),
        stderr: stderrBuffer.join("\\n")
      });
    } catch (err) {
      self.postMessage({ type: "error", error: String(err) });
    }
  }
};
`;

/**
 * Parse a pipeline command into individual commands.
 */
function parsePipeline(command: string): string[][] {
  // Split on | but not ||
  const parts = command.split(/(?<!\|)\|(?!\|)/);
  return parts.map(part => {
    const trimmed = part.trim();
    // Simple tokenization - split on whitespace, respecting quotes
    const tokens: string[] = [];
    let current = "";
    let inQuote = false;
    let quoteChar = "";

    for (const char of trimmed) {
      if (!inQuote && (char === '"' || char === "'")) {
        inQuote = true;
        quoteChar = char;
      } else if (inQuote && char === quoteChar) {
        inQuote = false;
        quoteChar = "";
      } else if (!inQuote && /\s/.test(char)) {
        if (current) {
          tokens.push(current);
          current = "";
        }
      } else {
        current += char;
      }
    }
    if (current) tokens.push(current);

    return tokens;
  }).filter(tokens => tokens.length > 0);
}

/**
 * Execute a pipeline using Worker-based isolation.
 * Each command runs in its own Worker, connected via SharedArrayBuffer pipes.
 */
async function executePipeline(
  options: ShellExecutionOptions
): Promise<ShellExecutionResult> {
  const start = Date.now();
  const commands = parsePipeline(options.command);

  if (commands.length === 0) {
    return {
      success: true,
      stdout: "",
      stderr: "",
      exit_code: 0,
      execution_time_ms: Date.now() - start,
    };
  }

  // For single command, use simple execution
  if (commands.length === 1) {
    return executeShellSimple({
      ...options,
      command: commands[0].join(" "),
    });
  }

  const busyboxDir = await resolveBusyboxDir(options.busyboxDir);
  const jsPath = "file://" + join(busyboxDir, "busybox.js");
  const wasmPath = "file://" + join(busyboxDir, "busybox.wasm");

  // Create pipes between commands
  const pipes: PipeBuffer[] = [];
  for (let i = 0; i < commands.length - 1; i++) {
    pipes.push(createPipeBuffer());
  }

  const blob = new Blob([BUSYBOX_WORKER_CODE], { type: "application/javascript" });
  const workerUrl = URL.createObjectURL(blob);
  const workers: Worker[] = [];

  try {
    // Start all workers
    const promises: Promise<{ exitCode: number; output: string; stderr: string }>[] = [];

    for (let i = 0; i < commands.length; i++) {
      const stdinBuffer = i > 0 ? pipes[i - 1].buffer : undefined;
      const stdoutBuffer = i < commands.length - 1 ? pipes[i].buffer : undefined;

      const worker = new Worker(workerUrl, { type: "module" });
      workers.push(worker);

      const promise = new Promise<{ exitCode: number; output: string; stderr: string }>((resolve, reject) => {
        worker.onmessage = (e) => {
          if (e.data.type === "ready") {
            worker.postMessage({
              type: "run",
              argv: ["busybox", ...commands[i]],
            });
          } else if (e.data.type === "done") {
            resolve({
              exitCode: e.data.exitCode,
              output: e.data.output || "",
              stderr: e.data.stderr || "",
            });
          } else if (e.data.type === "error") {
            reject(new Error(e.data.error));
          }
        };
        worker.onerror = (e) => reject(new Error(e.message));

        worker.postMessage({
          type: "init",
          jsPath,
          wasmPath,
          stdinBuffer,
          stdoutBuffer,
          files: options.files || {},
        });
      });

      promises.push(promise);
    }

    // Wait for all commands to complete
    const results = await Promise.all(promises);

    // Collect output from the last command
    const lastResult = results[results.length - 1];
    const allStderr = results.map(r => r.stderr).filter(Boolean).join("\n");

    return {
      success: lastResult.exitCode === 0,
      stdout: lastResult.output,
      stderr: allStderr,
      exit_code: lastResult.exitCode,
      execution_time_ms: Date.now() - start,
    };
  } catch (err) {
    return {
      success: false,
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exit_code: 1,
      execution_time_ms: Date.now() - start,
    };
  } finally {
    workers.forEach(w => w.terminate());
    URL.revokeObjectURL(workerUrl);
  }
}

/**
 * Execute shell command using ProcessManager (full shell support).
 * Supports pipes, variables, subshells, and other shell features.
 */
async function executeShellWithProcessManager(
  options: ShellExecutionOptions
): Promise<ShellExecutionResult> {
  const start = Date.now();
  const stdoutLines: string[] = [];
  const stderrLines: string[] = [];

  const manager = new ProcessManager({
    busyboxDir: options.busyboxDir,
    initialFiles: options.files,
    onStdout: (text) => stdoutLines.push(text),
    onStderr: (text) => stderrLines.push(text),
  });

  try {
    const pid = await manager.spawnShell(options.command);
    const result = await manager.waitForProcess(pid);

    return {
      success: result.exitCode === 0,
      stdout: stdoutLines.join("\n"),
      stderr: stderrLines.join("\n"),
      exit_code: result.exitCode,
      execution_time_ms: Date.now() - start,
      created_files: result.changedFiles.map((f) => ({
        path: f.path,
        content: Array.from(f.content),
      })),
    };
  } catch (err) {
    return {
      success: false,
      stdout: stdoutLines.join("\n"),
      stderr: err instanceof Error ? err.message : String(err),
      exit_code: 1,
      execution_time_ms: Date.now() - start,
    };
  } finally {
    manager.cleanup();
  }
}

/**
 * Execute shell command using simple applet invocation.
 * Faster but limited to basic commands without pipes/variables.
 */
async function executeShellSimple(options: ShellExecutionOptions): Promise<ShellExecutionResult> {
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
    // Improved error handling for non-Error objects
    let errMsg: string;
    if (err instanceof Error) {
      errMsg = err.message;
    } else if (err && typeof err === "object") {
      errMsg = JSON.stringify(err);
    } else {
      errMsg = String(err);
    }
    return {
      success: false,
      stdout: "",
      stderr: errMsg,
      exit_code: 1,
      execution_time_ms: Date.now() - start,
    };
  }
}

/**
 * Execute shell command, automatically choosing the best execution mode.
 * - Pipeline mode: Worker-based isolation for pipe commands
 * - Simple mode: Direct applet invocation for basic commands
 * - Full mode: ProcessManager for variables, subshells (TODO)
 */
async function executeShell(options: ShellExecutionOptions): Promise<ShellExecutionResult> {
  // Check for pipes first - use Worker-based pipeline execution
  if (hasPipes(options.command)) {
    return executePipeline(options);
  }

  // Check if command needs full shell support (variables, subshells, etc.)
  if (needsFullShell(options.command)) {
    // TODO: ProcessManager doesn't fully work yet for these cases
    // For now, fall back to simple execution with a warning
    console.error("Warning: Complex shell features not fully supported, attempting simple execution");
    return executeShellSimple(options);
  }

  // Use simple mode for basic commands
  return executeShellSimple(options);
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
