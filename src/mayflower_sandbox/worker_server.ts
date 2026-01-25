/**
 * Mayflower Sandbox - Long-Running Pyodide Worker
 *
 * JSON-RPC server that keeps Pyodide loaded in memory for fast execution.
 * Designed to be used in a process pool for 70-95% performance improvement.
 */

import { loadPyodide } from "npm:pyodide@0.28.3";
import { snapshotFiles, collectFilesFromPaths } from "./fs_utils.ts";

const START_TIME = Date.now();

interface ExecuteRequest {
  code: string;
  thread_id: string;
  stateful?: boolean;
  session_bytes?: number[];
  session_metadata?: Record<string, unknown>;
  files?: Record<string, number[]>;
  timeout_ms?: number;
}

interface ExecuteResult {
  success: boolean;
  stdout: string;
  stderr: string;
  result: unknown;
  session_bytes?: number[];
  session_metadata?: Record<string, unknown>;
  created_files?: Array<{ path: string; content: number[] }>;
  execution_time_ms: number;
}

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params: any;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number | string;
  result?: any;
  error?: {
    code: number;
    message: string;
  };
}

/**
 * Filter out micropip package loading messages from stdout
 * Same logic as legacy executor.ts
 */
function filterMicropipMessages(stdout: string): string {
  const lines = stdout.split("\n");
  const filtered = lines.filter((line) => {
    // Filter out micropip loading messages
    if (line.startsWith("Loading ")) return false;
    if (line.startsWith("Didn't find package ")) return false;
    if (line.startsWith("Package ") && line.includes(" loaded from ")) return false;
    if (line.startsWith("Loaded ")) return false;
    return true;
  });
  return filtered.join("\n");
}

// File operations (snapshotFiles, collectFiles, collectFilesFromPaths)
// are now imported from fs_utils.ts to reduce duplication

/**
 * Create stdout write handler
 */
function createStdoutHandler(
  buffer: { value: string },
  decoder: TextDecoder,
): { write: (buf: Uint8Array) => number } {
  return {
    write: (buf: Uint8Array) => {
      buffer.value += decoder.decode(buf, { stream: true });
      return buf.length;
    },
  };
}

/**
 * Create suppressed stdout handler
 */
function createSuppressedStdout(): { write: (buf: Uint8Array) => number } {
  return { write: (buf: Uint8Array) => buf.length };
}

/**
 * Restore session state from bytes
 */
async function restoreSession(
  pyodide: any,
  sessionBytes: number[],
): Promise<void> {
  await pyodide.runPythonAsync(`
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);

  await pyodide.runPythonAsync(`
_session_bytes = bytes(${JSON.stringify(Array.from(sessionBytes))})
_session_obj = cloudpickle.loads(_session_bytes)
globals().update(_session_obj)
`);
}

/**
 * Save session state to bytes
 */
async function saveSession(pyodide: any): Promise<number[]> {
  const sessionBytesResult = await pyodide.runPythonAsync(`
import types
_globals_snapshot = dict(globals())
_session_dict = {}
for k, v in _globals_snapshot.items():
    if k.startswith('_'):
        continue
    if isinstance(v, type) and v.__module__ == 'builtins':
        continue
    if hasattr(v, 'read') or hasattr(v, 'write'):
        continue
    _session_dict[k] = v
list(cloudpickle.dumps(_session_dict))
`);
  return sessionBytesResult.toJs();
}

/**
 * Mount files to Pyodide filesystem
 */
function mountFiles(pyodide: any, files: Record<string, number[]>): void {
  for (const [path, content] of Object.entries(files)) {
    const dir = path.substring(0, path.lastIndexOf("/"));
    if (dir && dir !== "/") {
      pyodide.FS.mkdirTree(dir);
    }
    pyodide.FS.writeFile(path, new Uint8Array(content));
  }
}

/**
 * Create file tracking delegate
 */
function createFileTracker(): {
  delegate: { onOpenFile: (path: string, flags: number) => void; onWriteToFile: (path: string, bytesWritten: number) => void };
  createdFiles: Set<string>;
  modifiedFiles: Set<string>;
} {
  const createdFiles = new Set<string>();
  const modifiedFiles = new Set<string>();
  return {
    delegate: {
      onOpenFile: (path: string, flags: number) => {
        if (flags & 0x200) createdFiles.add(path);
      },
      onWriteToFile: (path: string, bytesWritten: number) => {
        if (bytesWritten > 0) modifiedFiles.add(path);
      },
    },
    createdFiles,
    modifiedFiles,
  };
}

/**
 * Find files changed between snapshots
 */
function findChangedFiles(
  beforeSnapshot: Map<string, number>,
  afterSnapshot: Map<string, number>,
): string[] {
  const changed: string[] = [];
  for (const [path, size] of afterSnapshot) {
    const beforeSize = beforeSnapshot.get(path);
    if (beforeSize === undefined || beforeSize !== size) {
      changed.push(path);
    }
  }
  return changed;
}

/**
 * Long-running Pyodide worker
 */
class PyodideWorker {
  private pyodide: any = null;
  private initialized = false;
  private requestCount = 0;

  async initialize(): Promise<void> {
    if (this.initialized) return;

    console.error("[Worker] Loading Pyodide...");
    const start = Date.now();

    this.pyodide = await loadPyodide();
    this.pyodide.setStdout(createSuppressedStdout());
    await this.pyodide.loadPackage("micropip");

    await this.pyodide.runPythonAsync(`
import os
import sys
if 'matplotlib' not in sys.modules:
    os.environ['MPLBACKEND'] = 'Agg'
`);

    this.initialized = true;
    const elapsed = Date.now() - start;
    console.error(`[Worker] Ready in ${elapsed}ms (PID: ${Deno.pid})`);
  }

  async execute(params: ExecuteRequest): Promise<ExecuteResult> {
    const startTime = Date.now();
    this.requestCount++;

    const result: ExecuteResult = {
      success: false,
      stdout: "",
      stderr: "",
      result: null,
      execution_time_ms: 0,
    };

    try {
      const stdoutBuffer = { value: "" };
      const stderrBuffer = { value: "" };
      const stdoutDecoder = new TextDecoder();
      const stderrDecoder = new TextDecoder();

      this.pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
      this.pyodide.setStderr(createStdoutHandler(stderrBuffer, stderrDecoder));

      // Restore session if stateful
      if (params.stateful && params.session_bytes) {
        try {
          this.pyodide.setStdout(createSuppressedStdout());
          await restoreSession(this.pyodide, params.session_bytes);
          this.pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
        } catch (e) {
          stderrBuffer.value += `Session restore error: ${e}\n`;
          this.pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
        }
      }

      // Mount files
      if (params.files) {
        mountFiles(this.pyodide, params.files);
        this.pyodide.setStdout(createSuppressedStdout());
        await this.pyodide.runPythonAsync(`
import importlib
importlib.invalidate_caches()
`);
        this.pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
      }

      // Track file operations
      const tracker = createFileTracker();
      const beforeSnapshot = snapshotFiles(this.pyodide, ["/tmp", "/home"]);
      this.pyodide.FS.trackingDelegate = tracker.delegate;

      // Execute code
      try {
        result.result = await this.pyodide.runPythonAsync(params.code);
        result.success = true;
      } catch (e) {
        stderrBuffer.value += `${e}\n`;
      }

      this.pyodide.FS.trackingDelegate = {};

      result.stdout = filterMicropipMessages(stdoutBuffer.value);
      result.stderr = stderrBuffer.value;

      // Save session if stateful and successful
      if (params.stateful && result.success) {
        try {
          this.pyodide.setStdout(createSuppressedStdout());
          await this.pyodide.runPythonAsync(`
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);
          this.pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
          result.session_bytes = await saveSession(this.pyodide);
          result.session_metadata = {
            ...params.session_metadata,
            last_modified: new Date().toISOString(),
          };
        } catch (e) {
          stderrBuffer.value += `Session save error: ${e}\n`;
          result.stderr = stderrBuffer.value;
          this.pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
        }
      }

      // Collect changed files
      const allChangedPaths = new Set([...tracker.createdFiles, ...tracker.modifiedFiles]);
      const afterSnapshot = snapshotFiles(this.pyodide, ["/tmp", "/home"]);
      const snapshotChanges = findChangedFiles(beforeSnapshot, afterSnapshot);
      snapshotChanges.forEach((p) => allChangedPaths.add(p));

      if (allChangedPaths.size > 0) {
        const changedFiles = collectFilesFromPaths(this.pyodide, Array.from(allChangedPaths));
        if (changedFiles.length > 0) {
          result.created_files = changedFiles;
        }
      }

      result.execution_time_ms = Date.now() - startTime;
      return result;
    } catch (e) {
      result.stderr += `Execution error: ${e}\n`;
      result.execution_time_ms = Date.now() - startTime;
      return result;
    }
  }

  async handleRequest(request: JsonRpcRequest): Promise<JsonRpcResponse> {
    const { id, method, params } = request;

    try {
      if (method === "execute") {
        const result = await this.execute(params as ExecuteRequest);
        return { jsonrpc: "2.0", id, result };
      } else if (method === "health") {
        return {
          jsonrpc: "2.0",
          id,
          result: {
            status: "healthy",
            request_count: this.requestCount,
            uptime_ms: Date.now() - START_TIME,
            pid: Deno.pid,
          },
        };
      } else if (method === "shutdown") {
        return {
          jsonrpc: "2.0",
          id,
          result: { status: "shutting_down" },
        };
      } else {
        throw new Error(`Unknown method: ${method}`);
      }
    } catch (error) {
      return {
        jsonrpc: "2.0",
        id,
        error: {
          code: -32603,
          message: String(error),
        },
      };
    }
  }

  async run(): Promise<void> {
    await this.initialize();

    // Read JSON-RPC requests from stdin line-by-line
    const decoder = new TextDecoder();
    const buffer = new Uint8Array(65536);
    let leftover = "";

    while (true) {
      const bytesRead = await Deno.stdin.read(buffer);
      if (bytesRead === null) break;

      const chunk = decoder.decode(buffer.subarray(0, bytesRead));
      const lines = (leftover + chunk).split("\n");
      leftover = lines.pop() || "";

      for (const line of lines) {
        if (!line.trim()) continue;

        try {
          const request = JSON.parse(line) as JsonRpcRequest;
          const response = await this.handleRequest(request);

          // Check for shutdown
          if (request.method === "shutdown") {
            await Deno.stdout.write(
              new TextEncoder().encode(JSON.stringify(response) + "\n"),
            );
            Deno.exit(0);
          }

          // Write response to stdout
          await Deno.stdout.write(
            new TextEncoder().encode(JSON.stringify(response) + "\n"),
          );
        } catch (error) {
          console.error(`[Worker] Error processing request:`, error);
        }
      }
    }
  }
}

// Start worker
if (import.meta.main) {
  const worker = new PyodideWorker();
  await worker.run();
}
