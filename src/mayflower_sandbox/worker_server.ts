/**
 * Mayflower Sandbox - Long-Running Pyodide Worker
 *
 * JSON-RPC server that keeps Pyodide loaded in memory for fast execution.
 * Designed to be used in a process pool for 70-95% performance improvement.
 */

import { loadPyodide } from "npm:pyodide@0.28.3";

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

/**
 * Create a snapshot of file metadata (path + size) for comparison
 */
function snapshotFiles(pyodide: any, paths: string[]): Map<string, number> {
  const snapshot = new Map<string, number>();

  for (const path of paths) {
    try {
      const exists = pyodide.FS.analyzePath(path).exists;
      if (!exists) continue;

      const stat = pyodide.FS.stat(path);
      if (pyodide.FS.isDir(stat.mode)) {
        const entries = pyodide.FS.readdir(path);
        for (const entry of entries) {
          if (entry === "." || entry === "..") continue;
          const fullPath = path === "/" ? `/${entry}` : `${path}/${entry}`;
          const subSnapshot = snapshotFiles(pyodide, [fullPath]);
          subSnapshot.forEach((size, filePath) => snapshot.set(filePath, size));
        }
      } else {
        snapshot.set(path, stat.size);
      }
    } catch (_e) {
      // Skip files we can't read
    }
  }

  return snapshot;
}

/**
 * Collect files from Pyodide filesystem
 */
function collectFiles(pyodide: any, paths: string[]): Array<{ path: string; content: number[] }> {
  const files: Array<{ path: string; content: number[] }> = [];

  for (const path of paths) {
    try {
      const exists = pyodide.FS.analyzePath(path).exists;
      if (!exists) continue;

      const stat = pyodide.FS.stat(path);
      if (pyodide.FS.isDir(stat.mode)) {
        const entries = pyodide.FS.readdir(path);
        for (const entry of entries) {
          if (entry === "." || entry === "..") continue;
          const fullPath = path === "/" ? `/${entry}` : `${path}/${entry}`;
          files.push(...collectFiles(pyodide, [fullPath]));
        }
      } else {
        const content = pyodide.FS.readFile(path);
        files.push({
          path,
          content: Array.from(content),
        });
      }
    } catch (_e) {
      // Skip files we can't read
    }
  }

  return files;
}

/**
 * Collect files from specific paths (used with FS.trackingDelegate)
 */
function collectFilesFromPaths(
  pyodide: any,
  paths: string[],
): Array<{ path: string; content: number[] }> {
  const files: Array<{ path: string; content: number[] }> = [];

  for (const path of paths) {
    // Filter out system paths - agent should not know these exist
    if (path.startsWith('/lib') || path.startsWith('/share')) continue;

    try {
      const exists = pyodide.FS.analyzePath(path).exists;
      if (!exists) continue;

      const stat = pyodide.FS.stat(path);
      if (pyodide.FS.isDir(stat.mode)) continue; // Skip directories

      const content = pyodide.FS.readFile(path);
      files.push({ path, content: Array.from(content) });
    } catch (_e) {
      // Skip files we can't read
    }
  }

  return files;
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

    // Suppress stdout during micropip loading (it writes to stdout)
    this.pyodide.setStdout({ write: (buf: Uint8Array) => buf.length });

    await this.pyodide.loadPackage("micropip");

    // Leave stdout suppressed (will be set per-request in execute())

    // Pre-configure environment
    await this.pyodide.runPythonAsync(`
import os
import sys

# Set Agg backend for matplotlib (required for Deno/Node.js/non-browser contexts)
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
      // Capture stdout/stderr
      // Use 'write' handler like legacy executor for consistent newline handling
      let stdoutBuffer = "";
      let stderrBuffer = "";

      const stdoutDecoder = new TextDecoder();
      const stderrDecoder = new TextDecoder();

      this.pyodide.setStdout({
        write: (buf: Uint8Array) => {
          stdoutBuffer += stdoutDecoder.decode(buf, { stream: true });
          return buf.length;
        },
      });
      this.pyodide.setStderr({
        write: (buf: Uint8Array) => {
          stderrBuffer += stderrDecoder.decode(buf, { stream: true });
          return buf.length;
        },
      });

      // Load session state if provided
      if (params.stateful && params.session_bytes) {
        try {
          // Suppress stdout during cloudpickle installation
          this.pyodide.setStdout({ write: (buf: Uint8Array) => buf.length });

          await this.pyodide.runPythonAsync(`
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);

          // Restore stdout capture
          this.pyodide.setStdout({ write: (buf: Uint8Array) => { stdoutBuffer += stdoutDecoder.decode(buf, { stream: true }); return buf.length; } });

          // Restore session
          await this.pyodide.runPythonAsync(`
_session_bytes = bytes(${JSON.stringify(Array.from(params.session_bytes))})
_session_obj = cloudpickle.loads(_session_bytes)
globals().update(_session_obj)
`);
        } catch (e) {
          stderrBuffer += `Session restore error: ${e}\n`;
          this.pyodide.setStdout({ write: (buf: Uint8Array) => { stdoutBuffer += stdoutDecoder.decode(buf, { stream: true }); return buf.length; } });
        }
      }

      // Mount files if provided
      if (params.files) {
        for (const [path, content] of Object.entries(params.files)) {
          const dir = path.substring(0, path.lastIndexOf("/"));
          if (dir && dir !== "/") {
            this.pyodide.FS.mkdirTree(dir);
          }
          this.pyodide.FS.writeFile(path, new Uint8Array(content));
        }

        // Suppress stdout during import cache invalidation
        this.pyodide.setStdout({ write: (buf: Uint8Array) => buf.length });

        // Invalidate Python import cache
        await this.pyodide.runPythonAsync(`
import importlib
importlib.invalidate_caches()
`);

        // Restore stdout capture
        this.pyodide.setStdout({ write: (buf: Uint8Array) => { stdoutBuffer += stdoutDecoder.decode(buf, { stream: true }); return buf.length; } });
      }

      // Track file operations during execution using FS.trackingDelegate
      const createdFiles = new Set<string>();
      const modifiedFiles = new Set<string>();

      // Snapshot filesystem BEFORE execution for fallback detection
      // This catches files created by compiled libraries (matplotlib, PIL) that
      // may not trigger FS.trackingDelegate callbacks
      const beforeSnapshot = snapshotFiles(this.pyodide, ["/tmp", "/home"]);

      // Install tracking delegate before execution
      this.pyodide.FS.trackingDelegate = {
        onOpenFile: (path: string, flags: number) => {
          // flags & 0x200 (O_CREAT) means file is being created
          if (flags & 0x200) {
            createdFiles.add(path);
          }
        },
        onWriteToFile: (path: string, bytesWritten: number) => {
          if (bytesWritten > 0) {
            modifiedFiles.add(path);
          }
        },
      };

      // Execute code
      try {
        const execResult = await this.pyodide.runPythonAsync(params.code);
        result.result = execResult;
        result.success = true;
      } catch (e) {
        stderrBuffer += `${e}\n`;
        result.success = false;
      }

      // Remove tracking delegate
      this.pyodide.FS.trackingDelegate = {};

      result.stdout = filterMicropipMessages(stdoutBuffer);
      result.stderr = stderrBuffer;

      // Save session state if stateful
      if (params.stateful && result.success) {
        try {
          this.pyodide.setStdout({ write: (buf: Uint8Array) => buf.length });

          await this.pyodide.runPythonAsync(`
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);

          this.pyodide.setStdout({ write: (buf: Uint8Array) => { stdoutBuffer += stdoutDecoder.decode(buf, { stream: true }); return buf.length; } });

          const sessionBytesResult = await this.pyodide.runPythonAsync(`
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
          result.session_bytes = sessionBytesResult.toJs();
          result.session_metadata = {
            ...params.session_metadata,
            last_modified: new Date().toISOString(),
          };
        } catch (e) {
          stderrBuffer += `Session save error: ${e}\n`;
          result.stderr = stderrBuffer;
          this.pyodide.setStdout({ write: (buf: Uint8Array) => { stdoutBuffer += stdoutDecoder.decode(buf, { stream: true }); return buf.length; } });
        }
      }

      // Collect all tracked files (created OR modified) with contents for VFS persistence
      const allChangedPaths = new Set([...createdFiles, ...modifiedFiles]);

      // Fallback: Compare before/after filesystem snapshots to catch files
      // created by compiled libraries (matplotlib, PIL) that bypass FS.trackingDelegate
      const afterSnapshot = snapshotFiles(this.pyodide, ["/tmp", "/home"]);
      for (const [path, size] of afterSnapshot) {
        const beforeSize = beforeSnapshot.get(path);
        // New file (not in before) or modified file (different size)
        if (beforeSize === undefined || beforeSize !== size) {
          allChangedPaths.add(path);
        }
      }

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
