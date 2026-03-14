/**
 * Mayflower Sandbox - Busybox Shell Executor (WASM + VFS)
 *
 * Shell workflows are evaluated with POSIX-style precedence for:
 * - `|` pipelines
 * - `&&` / `||` AND-OR lists
 * - `;` sequential lists
 *
 * BusyBox `sh -c` is not used because `waitpid`/`fork` are not available in
 * this environment. Instead, this file parses the shell subset directly and
 * executes applets against BusyBox WASM modules.
 */

import { parseArgs } from "jsr:@std/cli@1.0.23/parse-args";
import { fromFileUrl, join, resolve, toFileUrl } from "jsr:@std/path@1";

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
  callMain: (args: string[]) => number;
  quit?: (status: number, toThrow?: unknown) => void;
  print?: (text: string) => void;
  printErr?: (text: string) => void;
  stdin?: () => number | null;
  thisProgram?: string;
  noExitRuntime?: boolean;
  noInitialRun?: boolean;
};

type BusyboxModuleFactory = (
  options: Record<string, unknown>,
) => Promise<BusyboxModule>;
type FileMap = Record<string, Uint8Array>;

const IGNORE_PREFIXES = ["/dev", "/proc", "/sys"];
const CONTROL_OPERATORS = new Set(["|", "&&", "||", ";"]);
const REDIRECT_OPERATORS = new Set([">", ">>", "<"]);

interface ParsedCommand {
  argv: string[];
  redirectOut?: string;
  redirectAppend?: string;
  redirectIn?: string;
}

interface Token {
  type: "word" | "operator";
  value: string;
}

interface SequenceNode {
  type: "sequence";
  items: AndOrNode[];
}

interface AndOrNode {
  type: "and_or";
  first: ExecutableNode;
  rest: Array<{ operator: "&&" | "||"; right: ExecutableNode }>;
}

interface SimpleCommandNode {
  type: "command";
  command: ParsedCommand;
}

interface PipelineNode {
  type: "pipeline";
  commands: ParsedCommand[];
}

type ExecutableNode = SimpleCommandNode | PipelineNode;

interface OutputCapture {
  stdout: string[];
  stderr: string[];
  currentRedirect?: string;
  redirectBuffer?: string[];
}

interface StdinState {
  read: () => number | null;
}

interface CommandExecutionResult {
  stdout: string;
  stderr: string;
  exitCode: number;
  changedFiles: Array<{ path: string; content: number[] }>;
}

interface EvaluationState {
  files: FileMap;
  stdoutParts: string[];
  stderrParts: string[];
  changedFiles: Map<string, Uint8Array>;
  lastExitCode: number;
}

interface WorkerExecutionResult {
  exitCode: number;
  output: string;
  stderr: string;
  createdFiles: Array<{ path: string; content: number[] }>;
}

function cloneFiles(files: FileMap = {}): FileMap {
  const cloned: FileMap = {};
  for (const [path, content] of Object.entries(files)) {
    cloned[path] = new Uint8Array(content);
  }
  return cloned;
}

function tokenizeShell(command: string): Token[] {
  const tokens: Token[] = [];
  let current = "";
  let inQuote: string | null = null;
  let index = 0;

  function pushWord(): void {
    if (current) {
      tokens.push({ type: "word", value: current });
      current = "";
    }
  }

  while (index < command.length) {
    const char = command[index];

    if (inQuote) {
      current += char;
      if (char === inQuote) {
        inQuote = null;
      }
      index++;
      continue;
    }

    if (char === '"' || char === "'") {
      current += char;
      inQuote = char;
      index++;
      continue;
    }

    if (char === " " || char === "\t" || char === "\n") {
      pushWord();
      index++;
      continue;
    }

    const nextTwo = command.slice(index, index + 2);
    if (nextTwo === "&&" || nextTwo === "||" || nextTwo === ">>") {
      pushWord();
      tokens.push({ type: "operator", value: nextTwo });
      index += 2;
      continue;
    }

    if (char === "|" || char === ";" || char === ">" || char === "<") {
      pushWord();
      tokens.push({ type: "operator", value: char });
      index++;
      continue;
    }

    current += char;
    index++;
  }

  pushWord();
  return tokens;
}

function parseWord(raw: string): string {
  const parsed = parseArgv(raw);
  return parsed[0] ?? "";
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

function parseRedirectTarget(tokens: Token[], index: number): {
  target: string;
  nextIndex: number;
} {
  const targetToken = tokens[index + 1];
  if (!targetToken || targetToken.type !== "word") {
    throw new Error(`Expected redirect target after ${tokens[index].value}`);
  }

  const target = parseWord(targetToken.value);
  if (!target) {
    throw new Error(`Expected redirect target after ${tokens[index].value}`);
  }

  return { target, nextIndex: index + 2 };
}

function applyRedirect(result: ParsedCommand, operator: string, target: string): void {
  switch (operator) {
    case ">":
      result.redirectOut = target;
      break;
    case ">>":
      result.redirectAppend = target;
      break;
    case "<":
      result.redirectIn = target;
      break;
    default:
      throw new Error(`Unexpected operator ${operator} inside simple command`);
  }
}

function parseSimpleCommandTokens(tokens: Token[]): ParsedCommand {
  const result: ParsedCommand = { argv: [] };
  let index = 0;

  while (index < tokens.length) {
    const token = tokens[index];
    if (token.type === "operator") {
      if (!REDIRECT_OPERATORS.has(token.value)) {
        throw new Error(`Unexpected operator ${token.value} inside simple command`);
      }

      const { target, nextIndex } = parseRedirectTarget(tokens, index);
      applyRedirect(result, token.value, target);
      index = nextIndex;
      continue;
    }

    result.argv.push(parseWord(token.value));
    index++;
  }

  return result;
}

class ShellParser {
  constructor(private readonly tokens: Token[], private index = 0) {}

  parse(): SequenceNode {
    if (this.tokens.length === 0) {
      return { type: "sequence", items: [] };
    }

    const items: AndOrNode[] = [this.parseAndOr()];
    while (this.match(";")) {
      if (this.isAtEnd()) break;
      items.push(this.parseAndOr());
    }

    if (!this.isAtEnd()) {
      throw new Error(`Unexpected token ${this.peek()?.value ?? ""}`);
    }

    return { type: "sequence", items };
  }

  private parseAndOr(): AndOrNode {
    const first = this.parsePipeline();
    const rest: Array<{ operator: "&&" | "||"; right: ExecutableNode }> = [];

    while (this.match("&&", "||")) {
      const operator = this.previous().value as "&&" | "||";
      rest.push({ operator, right: this.parsePipeline() });
    }

    return { type: "and_or", first, rest };
  }

  private parsePipeline(): ExecutableNode {
    const commands: ParsedCommand[] = [this.parseSimpleCommand().command];
    while (this.match("|")) {
      commands.push(this.parseSimpleCommand().command);
    }

    if (commands.length === 1) {
      return { type: "command", command: commands[0] };
    }

    return { type: "pipeline", commands };
  }

  private parseSimpleCommand(): SimpleCommandNode {
    const tokens: Token[] = [];
    while (!this.isAtEnd() && !this.checkControlOperator()) {
      tokens.push(this.advance());
    }

    if (tokens.length === 0) {
      throw new Error(
        `Expected command before ${this.peek()?.value ?? "end of input"}`,
      );
    }

    return {
      type: "command",
      command: parseSimpleCommandTokens(tokens),
    };
  }

  private checkControlOperator(): boolean {
    const token = this.peek();
    return !!token && token.type === "operator" &&
      CONTROL_OPERATORS.has(token.value);
  }

  private match(...operators: string[]): boolean {
    const token = this.peek();
    if (
      !token || token.type !== "operator" || !operators.includes(token.value)
    ) {
      return false;
    }
    this.index++;
    return true;
  }

  private advance(): Token {
    const token = this.tokens[this.index];
    this.index++;
    return token;
  }

  private previous(): Token {
    return this.tokens[this.index - 1];
  }

  private peek(): Token | undefined {
    return this.tokens[this.index];
  }

  private isAtEnd(): boolean {
    return this.index >= this.tokens.length;
  }
}

export function parseShellExpression(command: string): SequenceNode {
  return new ShellParser(tokenizeShell(command)).parse();
}

function isExitStatusError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const name = (err as { name?: string }).name;
  const message = (err as { message?: string }).message;
  return name === "ExitStatus" || message === "ExitStatus";
}

const ENOENT = 44;
const EEXIST = 20;
const ENOTDIR = 54;

function getErrorCode(err: unknown): string {
  const info = err as
    | { code?: string; errno?: string | number; name?: string }
    | undefined;
  if (info?.code) return info.code;
  if (info?.name === "ErrnoError") {
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
      if (
        code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")
      ) {
        throw err;
      }
    }
  }
}

function overwriteRedirectContent(fs: any, path: string, content: string): void {
  fs.writeFile(path, content);
}

function appendRedirectContent(fs: any, path: string, content: string): void {
  try {
    const existing = fs.readFile(path, { encoding: "utf8" }) as string;
    fs.writeFile(path, existing + content);
  } catch (err) {
    const code = getErrorCode(err);
    if (code === "ENOENT") {
      fs.writeFile(path, content);
    } else {
      throw err;
    }
  }
}

function createByteReader(bytes: Uint8Array): () => number | null {
  let offset = 0;
  return () => {
    if (offset >= bytes.length) return null;
    return bytes[offset++];
  };
}

function createDefaultStdinReader(): () => number | null {
  return () => null;
}

function createRedirectContent(buffer: string[]): string {
  return buffer.join("\n") + (buffer.length ? "\n" : "");
}

function beginOutputRedirection(
  output: OutputCapture,
  command: ParsedCommand,
): void {
  output.currentRedirect = command.redirectOut || command.redirectAppend;
  output.redirectBuffer = [];
}

function handleRedirection(
  module: BusyboxModule,
  cmd: ParsedCommand,
  output: OutputCapture,
): number {
  if (!output.redirectBuffer) return 0;

  const content = createRedirectContent(output.redirectBuffer);
  const path = cmd.redirectOut || cmd.redirectAppend!;

  try {
    ensureParentDir(module.FS, path);
    if (cmd.redirectAppend) {
      appendRedirectContent(module.FS, path, content);
    } else {
      overwriteRedirectContent(module.FS, path, content);
    }
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

function configureStdinReader(
  module: BusyboxModule,
  command: ParsedCommand,
  output: OutputCapture,
  stdinState: StdinState,
  defaultStdin: () => number | null,
): number | null {
  if (!command.redirectIn) {
    stdinState.read = defaultStdin;
    return null;
  }

  try {
    const inputData = module.FS.readFile(command.redirectIn, {
      encoding: "binary",
    }) as Uint8Array;
    stdinState.read = createByteReader(inputData);
    return null;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    output.stderr.push(`Cannot redirect from ${command.redirectIn}: ${errMsg}`);
    return 1;
  }
}

function runApplet(module: BusyboxModule, argv: string[]): number {
  let exitCode = 0;
  const originalQuit = module.quit;

  module.quit = (status: number, toThrow?: unknown) => {
    exitCode = status;
    if (toThrow) throw toThrow;
    throw new Error("ExitStatus");
  };

  try {
    const result = module.callMain(["busybox", ...argv]);
    if (typeof result === "number") {
      exitCode = result;
    }
  } catch (err) {
    if (!isExitStatusError(err)) throw err;
  } finally {
    module.quit = originalQuit;
  }

  return exitCode;
}

function executeApplet(
  module: BusyboxModule,
  cmd: ParsedCommand,
  output: OutputCapture,
  stdinState: StdinState,
  defaultStdin: () => number | null = createDefaultStdinReader(),
): number {
  if (cmd.argv.length === 0) return 0;

  const hasRedirect = !!(cmd.redirectOut || cmd.redirectAppend);
  const originalStdin = stdinState.read;

  const stdinError = configureStdinReader(
    module,
    cmd,
    output,
    stdinState,
    defaultStdin,
  );
  if (stdinError !== null) {
    stdinState.read = originalStdin;
    return stdinError;
  }

  if (hasRedirect) {
    beginOutputRedirection(output, cmd);
  }

  let exitCode = 0;
  try {
    exitCode = runApplet(module, cmd.argv);
  } finally {
    stdinState.read = originalStdin;
  }

  if (hasRedirect) {
    const redirectResult = handleRedirection(module, cmd, output);
    if (redirectResult !== 0) exitCode = redirectResult;
  }

  return exitCode;
}

async function readStdinFiles(): Promise<FileMap> {
  const files: FileMap = {};
  const chunks: Uint8Array[] = [];
  const buffer = new Uint8Array(8192);

  while (true) {
    const bytesRead = await Deno.stdin.read(buffer);
    if (bytesRead === null) break;
    chunks.push(buffer.slice(0, bytesRead));
  }

  const stdinData = new Uint8Array(
    chunks.reduce((acc, chunk) => acc + chunk.length, 0),
  );
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
      if (
        code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")
      ) {
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
      if (
        code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")
      ) {
        throw err;
      }
    }
  }
}

function normalizePath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

async function mountFiles(fs: any, files: FileMap): Promise<void> {
  for (const [path, content] of Object.entries(files)) {
    const normalized = normalizePath(path);
    ensureDir(fs, normalized);
    fs.writeFile(normalized, content, { encoding: "binary" });
  }
}

function statStamp(stat: any): string {
  let mtime = 0;
  if (typeof stat.mtime === "number") {
    mtime = stat.mtime;
  } else if (stat.mtime instanceof Date) {
    mtime = stat.mtime.getTime();
  }
  return `${mtime}:${stat.size ?? 0}`;
}

function shouldIgnorePath(path: string): boolean {
  return IGNORE_PREFIXES.some((prefix) =>
    path === prefix || path.startsWith(`${prefix}/`)
  );
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
    if (code === "ENOENT" || code === "ENOTDIR") {
      return [];
    }
    throw err;
  }
}

function processEntry(
  fs: any,
  entryPath: string,
  snapshot: Map<string, string>,
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
    if (code !== "ENOENT") {
      throw err;
    }
  }
}

function snapshotFs(fs: any, root: string): Map<string, string> {
  const snapshot = new Map<string, string>();
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

function findChangedFiles(
  before: Map<string, string>,
  after: Map<string, string>,
): string[] {
  const changed: string[] = [];
  for (const [path, stamp] of after.entries()) {
    if (!before.has(path) || before.get(path) !== stamp) {
      changed.push(path);
    }
  }
  return changed;
}

function collectFiles(
  fs: any,
  paths: string[],
): Array<{ path: string; content: number[] }> {
  const collected: Array<{ path: string; content: number[] }> = [];
  for (const path of paths) {
    try {
      const content = fs.readFile(path, { encoding: "binary" }) as Uint8Array;
      collected.push({ path, content: Array.from(content) });
    } catch (err) {
      const code = getErrorCode(err);
      if (code !== "ENOENT") {
        console.error(
          `Warning: Could not read file ${path}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
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
  candidates.push(
    resolve(scriptDir, "../../../..", "busybox-wasm", "build", "wasm"),
  );

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

async function loadBusyboxModule(
  dir: string,
  output: OutputCapture,
  stdinState: StdinState,
): Promise<BusyboxModule> {
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
    thisProgram: "busybox",
    print: (text: string) => {
      if (output.currentRedirect) {
        output.redirectBuffer?.push(text);
      } else {
        output.stdout.push(text);
      }
    },
    printErr: (text: string) => output.stderr.push(text),
    stdin: () => stdinState.read(),
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

async function executeSimpleCommand(
  command: ParsedCommand,
  files: FileMap,
  busyboxDir: string,
): Promise<CommandExecutionResult> {
  const output: OutputCapture = { stdout: [], stderr: [] };
  const stdinState: StdinState = { read: createDefaultStdinReader() };
  const module = await loadBusyboxModule(busyboxDir, output, stdinState);
  ensureBaseDirs(module.FS);
  await mountFiles(module.FS, files);

  const before = snapshotFs(module.FS, "/");
  const exitCode = executeApplet(module, command, output, stdinState);
  const after = snapshotFs(module.FS, "/");
  const changedFiles = collectFiles(module.FS, findChangedFiles(before, after));

  return {
    stdout: output.stdout.join("\n"),
    stderr: output.stderr.join("\n"),
    exitCode,
    changedFiles,
  };
}

const PIPE_HEADER_SIZE = 16;
const PIPE_BUFFER_SIZE = 8192;

interface PipeBuffer {
  buffer: SharedArrayBuffer;
  control: Int32Array;
}

function createPipeBuffer(): PipeBuffer {
  const buffer = new SharedArrayBuffer(PIPE_HEADER_SIZE + PIPE_BUFFER_SIZE);
  const control = new Int32Array(buffer, 0, 4);
  Atomics.store(control, 0, 0);
  Atomics.store(control, 1, 0);
  Atomics.store(control, 2, 0);
  return { buffer, control };
}

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
}

const ENOENT = 44;
const EEXIST = 20;
const ENOTDIR = 54;
let stdoutPipe = null;
let stdinPipe = null;
let module = null;
let vfsFiles = {};
let command = null;
const outputBuffer = [];
const stderrBuffer = [];
let currentRedirect = null;
let redirectBuffer = [];
let currentStdin = () => null;

function getErrorCode(err) {
  if (err && err.code) return err.code;
  if (err && err.name === "ErrnoError") {
    const errno = err.errno;
    if (errno === ENOENT || errno === 44) return "ENOENT";
    if (errno === EEXIST || errno === 17) return "EEXIST";
    if (errno === ENOTDIR || errno === 20) return "ENOTDIR";
    return "ERRNO_" + errno;
  }
  if (err && typeof err.errno === "string") return err.errno;
  return "";
}

function ensureDir(FS, path) {
  const parts = path.split("/").filter(Boolean);
  let current = "";
  for (const part of parts.slice(0, -1)) {
    current += "/" + part;
    try {
      FS.mkdir(current);
    } catch (err) {
      const code = getErrorCode(err);
      if (code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")) {
        throw err;
      }
    }
  }
}

function ensureBaseDirs(FS) {
  for (const dir of ["/tmp", "/home", "/root", "/bin"]) {
    try {
      FS.mkdir(dir);
    } catch (err) {
      const code = getErrorCode(err);
      if (code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")) {
        throw err;
      }
    }
  }
}

function snapshotFs(FS, path) {
  const snapshot = new Map();
  const stack = [path];
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current || current === "/dev" || current.startsWith("/dev/") || current === "/proc" || current.startsWith("/proc/") || current === "/sys" || current.startsWith("/sys/")) {
      continue;
    }
    let entries = [];
    try {
      entries = FS.readdir(current);
    } catch (err) {
      const code = getErrorCode(err);
      if (code === "ENOENT" || code === "ENOTDIR") continue;
      throw err;
    }
    for (const entry of entries) {
      if (entry === "." || entry === "..") continue;
      const fullPath = current === "/" ? "/" + entry : current + "/" + entry;
      if (fullPath === "/dev" || fullPath.startsWith("/dev/") || fullPath === "/proc" || fullPath.startsWith("/proc/") || fullPath === "/sys" || fullPath.startsWith("/sys/")) {
        continue;
      }
      try {
        const stat = FS.stat(fullPath);
        if (FS.isDir(stat.mode)) {
          stack.push(fullPath);
        } else if (FS.isFile(stat.mode)) {
          const mtime = typeof stat.mtime === "number"
            ? stat.mtime
            : (stat.mtime instanceof Date ? stat.mtime.getTime() : 0);
          const stamp = String(mtime) + ":" + String(stat.size || 0);
          snapshot.set(fullPath, stamp);
        }
      } catch (err) {
        if (getErrorCode(err) !== "ENOENT") throw err;
      }
    }
  }
  return snapshot;
}

function findChangedFiles(before, after) {
  const changed = [];
  for (const [path, stamp] of after.entries()) {
    if (!before.has(path) || before.get(path) !== stamp) {
      changed.push(path);
    }
  }
  return changed;
}

function collectFiles(FS, paths) {
  const files = [];
  for (const path of paths) {
    try {
      const content = FS.readFile(path, { encoding: "binary" });
      files.push({ path, content: Array.from(content) });
    } catch (err) {
      if (getErrorCode(err) !== "ENOENT") {
        stderrBuffer.push("Warning: Could not read file " + path + ": " + String(err));
      }
    }
  }
  return files;
}

function createByteReader(bytes) {
  let offset = 0;
  return () => {
    if (offset >= bytes.length) return null;
    return bytes[offset++];
  };
}

function handleRedirection(module, cmd) {
  if (!redirectBuffer) return 0;
  const content = redirectBuffer.join("\\n") + (redirectBuffer.length ? "\\n" : "");
  const path = cmd.redirectOut || cmd.redirectAppend;
  try {
    const dir = path.substring(0, path.lastIndexOf("/"));
    if (dir && dir !== "/") {
      try {
        module.FS.mkdirTree(dir);
      } catch (err) {
        const code = getErrorCode(err);
        if (code !== "EEXIST" && code !== "ENOTDIR" && !code.startsWith("ERRNO_")) throw err;
      }
    }
    if (cmd.redirectAppend) {
      try {
        const existing = module.FS.readFile(path, { encoding: "utf8" });
        module.FS.writeFile(path, existing + content);
      } catch (err) {
        if (getErrorCode(err) === "ENOENT") {
          module.FS.writeFile(path, content);
        } else {
          throw err;
        }
      }
    } else {
      module.FS.writeFile(path, content);
    }
    return 0;
  } catch (err) {
    stderrBuffer.push("Cannot redirect to " + path + ": " + String(err));
    return 1;
  } finally {
    currentRedirect = null;
    redirectBuffer = [];
  }
}

function executeApplet(module, cmd, defaultStdin) {
  if (!cmd.argv || cmd.argv.length === 0) return 0;
  let exitCode = 0;
  const originalQuit = module.quit;
  const originalStdin = currentStdin;
  if (cmd.redirectIn) {
    try {
      const inputData = module.FS.readFile(cmd.redirectIn, { encoding: "binary" });
      currentStdin = createByteReader(inputData);
    } catch (err) {
      stderrBuffer.push("Cannot redirect from " + cmd.redirectIn + ": " + String(err));
      currentStdin = originalStdin;
      return 1;
    }
  } else if (defaultStdin) {
    currentStdin = defaultStdin;
  } else {
    currentStdin = () => null;
  }
  if (cmd.redirectOut || cmd.redirectAppend) {
    currentRedirect = cmd.redirectOut || cmd.redirectAppend;
    redirectBuffer = [];
  }
  module.quit = (status, toThrow) => {
    exitCode = status;
    if (toThrow) throw toThrow;
    throw new Error("ExitStatus");
  };
  try {
    const result = module.callMain(["busybox", ...cmd.argv]);
    if (typeof result === "number") exitCode = result;
  } catch (err) {
    if (!err || (err.name !== "ExitStatus" && err.message !== "ExitStatus")) throw err;
  } finally {
    module.quit = originalQuit;
    currentStdin = originalStdin;
  }
  if (cmd.redirectOut || cmd.redirectAppend) {
    const redirectResult = handleRedirection(module, cmd);
    if (redirectResult !== 0) exitCode = redirectResult;
  }
  return exitCode;
}

self.onmessage = async (event) => {
  const msg = event.data;
  if (msg.type === "init") {
    try {
      if (msg.stdoutBuffer) stdoutPipe = new PipeWriter(msg.stdoutBuffer);
      if (msg.stdinBuffer) stdinPipe = new PipeReader(msg.stdinBuffer);
      if (msg.files) vfsFiles = msg.files;
      command = msg.command;

      const factory = (await import(msg.jsPath)).default;
      module = await factory({
        noInitialRun: true,
        noExitRuntime: true,
        thisProgram: "busybox",
        print: (text) => {
          if (currentRedirect) {
            redirectBuffer.push(text);
          } else if (stdoutPipe) {
            stdoutPipe.write(text + "\\n");
          } else {
            outputBuffer.push(text);
          }
        },
        printErr: (text) => stderrBuffer.push(text),
        stdin: () => currentStdin(),
        locateFile(path) {
          if (path.endsWith(".wasm")) return msg.wasmPath;
          return path;
        },
      });

      const FS = module.FS;
      ensureBaseDirs(FS);
      for (const [filePath, content] of Object.entries(vfsFiles)) {
        ensureDir(FS, filePath);
        const data = typeof content === "string" ? new TextEncoder().encode(content) : new Uint8Array(content);
        FS.writeFile(filePath, data);
      }

      self.postMessage({ type: "ready" });
    } catch (err) {
      self.postMessage({ type: "error", error: String(err) });
    }
    return;
  }

  if (msg.type === "run") {
    try {
      const before = snapshotFs(module.FS, "/");
      const defaultStdin = stdinPipe
        ? () => {
          const chunk = stdinPipe.read(1);
          if (chunk === null) return null;
          return chunk[0];
        }
        : undefined;
      const exitCode = executeApplet(module, command, defaultStdin);
      const after = snapshotFs(module.FS, "/");
      const createdFiles = collectFiles(module.FS, findChangedFiles(before, after));
      if (stdoutPipe) stdoutPipe.close();
      self.postMessage({
        type: "done",
        exitCode,
        output: outputBuffer.join("\\n"),
        stderr: stderrBuffer.join("\\n"),
        createdFiles,
      });
    } catch (err) {
      self.postMessage({ type: "error", error: String(err) });
    }
  }
};
`;

function mergePipelineChanges(
  stageResults: WorkerExecutionResult[],
): Array<{ path: string; content: number[] }> {
  const merged = new Map<string, number[]>();

  for (const result of stageResults) {
    for (const file of result.createdFiles) {
      if (merged.has(file.path)) {
        throw new Error(`Pipeline merge conflict for ${file.path}`);
      }
      merged.set(file.path, file.content);
    }
  }

  return Array.from(merged.entries()).map(([path, content]) => ({
    path,
    content,
  }));
}

async function executePipeline(
  commands: ParsedCommand[],
  files: FileMap,
  busyboxDir: string,
): Promise<CommandExecutionResult> {
  if (commands.length === 0) {
    return { stdout: "", stderr: "", exitCode: 0, changedFiles: [] };
  }

  if (commands.length === 1) {
    return executeSimpleCommand(commands[0], files, busyboxDir);
  }

  const jsPath = "file://" + join(busyboxDir, "busybox.js");
  const wasmPath = "file://" + join(busyboxDir, "busybox.wasm");
  const pipes: PipeBuffer[] = [];
  for (let i = 0; i < commands.length - 1; i++) {
    pipes.push(createPipeBuffer());
  }

  const blob = new Blob([BUSYBOX_WORKER_CODE], {
    type: "application/javascript",
  });
  const workerUrl = URL.createObjectURL(blob);
  const workers: Worker[] = [];

  try {
    const promises: Promise<WorkerExecutionResult>[] = [];

    for (let i = 0; i < commands.length; i++) {
      const stdinBuffer = i > 0 ? pipes[i - 1].buffer : undefined;
      const stdoutBuffer = i < commands.length - 1
        ? pipes[i].buffer
        : undefined;
      const worker = new Worker(workerUrl, { type: "module" });
      workers.push(worker);

      const promise = new Promise<WorkerExecutionResult>((resolve, reject) => {
        worker.onmessage = (event) => {
          if (event.data.type === "ready") {
            worker.postMessage({ type: "run" });
          } else if (event.data.type === "done") {
            resolve({
              exitCode: event.data.exitCode,
              output: event.data.output || "",
              stderr: event.data.stderr || "",
              createdFiles: event.data.createdFiles || [],
            });
          } else if (event.data.type === "error") {
            reject(new Error(event.data.error));
          }
        };
        worker.onerror = (event) => reject(new Error(event.message));
        worker.postMessage({
          type: "init",
          jsPath,
          wasmPath,
          stdinBuffer,
          stdoutBuffer,
          files: cloneFiles(files),
          command: commands[i],
        });
      });

      promises.push(promise);
    }

    const results = await Promise.all(promises);
    const lastResult = results[results.length - 1];
    const changedFiles = mergePipelineChanges(results);

    return {
      stdout: lastResult.output,
      stderr: results.map((result) => result.stderr).filter(Boolean).join("\n"),
      exitCode: lastResult.exitCode,
      changedFiles,
    };
  } finally {
    workers.forEach((worker) => worker.terminate());
    URL.revokeObjectURL(workerUrl);
  }
}

function applyChangedFiles(
  state: EvaluationState,
  changedFiles: Array<{ path: string; content: number[] }>,
): void {
  for (const file of changedFiles) {
    const content = new Uint8Array(file.content);
    state.files[file.path] = content;
    state.changedFiles.set(file.path, content);
  }
}

function appendOutput(parts: string[], output: string): void {
  if (output) {
    parts.push(output);
  }
}

async function executeNode(
  node: ExecutableNode,
  state: EvaluationState,
  busyboxDir: string,
): Promise<void> {
  const result = node.type === "pipeline"
    ? await executePipeline(node.commands, state.files, busyboxDir)
    : await executeSimpleCommand(node.command, state.files, busyboxDir);

  appendOutput(state.stdoutParts, result.stdout);
  appendOutput(state.stderrParts, result.stderr);
  applyChangedFiles(state, result.changedFiles);
  state.lastExitCode = result.exitCode;
}

async function evaluateSequence(
  sequence: SequenceNode,
  state: EvaluationState,
  busyboxDir: string,
): Promise<void> {
  for (const item of sequence.items) {
    await executeNode(item.first, state, busyboxDir);

    for (const segment of item.rest) {
      const shouldRun = segment.operator === "&&"
        ? state.lastExitCode === 0
        : state.lastExitCode !== 0;
      if (!shouldRun) {
        continue;
      }
      await executeNode(segment.right, state, busyboxDir);
    }
  }
}

export async function executeShell(
  options: ShellExecutionOptions,
): Promise<ShellExecutionResult> {
  const start = Date.now();
  const state: EvaluationState = {
    files: cloneFiles(options.files || {}),
    stdoutParts: [],
    stderrParts: [],
    changedFiles: new Map(),
    lastExitCode: 0,
  };

  try {
    const busyboxDir = await resolveBusyboxDir(options.busyboxDir);
    const sequence = parseShellExpression(options.command);
    await evaluateSequence(sequence, state, busyboxDir);

    const createdFiles = state.changedFiles.size > 0
      ? Array.from(state.changedFiles.entries()).map(([path, content]) => ({
        path,
        content: Array.from(content),
      }))
      : undefined;

    return {
      success: state.lastExitCode === 0,
      stdout: state.stdoutParts.join("\n"),
      stderr: state.stderrParts.join("\n"),
      exit_code: state.lastExitCode,
      execution_time_ms: Date.now() - start,
      created_files: createdFiles,
    };
  } catch (err) {
    const stderr = [
      ...state.stderrParts,
      err instanceof Error ? err.message : String(err),
    ].filter(Boolean).join("\n");

    return {
      success: false,
      stdout: state.stdoutParts.join("\n"),
      stderr,
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
    console.error(
      "Usage: shell_executor.ts --command '<cmd>' [--busybox-dir <path>]",
    );
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
