/**
 * Mayflower Sandbox - Pyodide Executor
 *
 * Clean, minimal Pyodide executor with stdin/stdout protocol.
 * No dependency on langchain-sandbox - written from scratch.
 */

import { loadPyodide } from "npm:pyodide@0.28.3";
import { parseArgs } from "jsr:@std/cli@1.0.23/parse-args";

interface ExecutionOptions {
  code: string;
  files?: Record<string, Uint8Array>;
  stateful?: boolean;
  sessionBytes?: Uint8Array;
  sessionMetadata?: Record<string, unknown>;
}

interface ExecutionResult {
  success: boolean;
  stdout: string;
  stderr: string;
  result: unknown;
  sessionBytes?: number[];
  sessionMetadata?: Record<string, unknown>;
  files?: Array<{ path: string; content: number[] }>;
}

/**
 * Filter out micropip package loading messages from stdout
 */
function filterMicropipMessages(stdout: string): string {
  const lines = stdout.split('\n');
  const filtered = lines.filter(line => {
    // Filter out micropip loading messages
    if (line.startsWith('Loading ')) return false;
    if (line.startsWith('Didn\'t find package ')) return false;
    if (line.startsWith('Package ') && line.includes(' loaded from ')) return false;
    if (line.startsWith('Loaded ')) return false;
    return true;
  });
  return filtered.join('\n');
}

/**
 * Read binary file data from stdin
 */
async function readStdinFiles(): Promise<Record<string, Uint8Array>> {
  const files: Record<string, Uint8Array> = {};

  // Read all stdin data (modern Deno API)
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

  // Parse binary protocol: "MFS\x01" + length(4) + JSON metadata + file contents
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
        // Store file path and size
        snapshot.set(path, stat.size);
      }
    } catch (e) {
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
      // Check if path exists
      const exists = pyodide.FS.analyzePath(path).exists;
      if (!exists) continue;

      // If directory, recurse
      const stat = pyodide.FS.stat(path);
      if (pyodide.FS.isDir(stat.mode)) {
        const entries = pyodide.FS.readdir(path);
        for (const entry of entries) {
          if (entry === "." || entry === "..") continue;
          const fullPath = path === "/" ? `/${entry}` : `${path}/${entry}`;
          files.push(...collectFiles(pyodide, [fullPath]));
        }
      } else {
        // Read file
        const content = pyodide.FS.readFile(path);
        files.push({
          path,
          content: Array.from(content),
        });
      }
    } catch (e) {
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
 * Execute Python code in Pyodide
 */
async function execute(options: ExecutionOptions): Promise<ExecutionResult> {
  const result: ExecutionResult = {
    success: false,
    stdout: "",
    stderr: "",
    result: null,
  };

  try {
    // Load Pyodide (let it use default paths from npm package)
    const pyodide = await loadPyodide();

    // Always load micropip - it's lightweight and commonly needed for package installation
    // (needed for dill in stateful mode, and for NumPy/SciPy packages in general)
    await pyodide.loadPackage("micropip");

    // Capture stdout/stderr
    // Use 'write' handler instead of 'batched' for more reliable output capture
    // especially with async code. See: https://github.com/pyodide/pyodide/issues/4139
    let stdoutBuffer = "";
    let stderrBuffer = "";

    const stdoutDecoder = new TextDecoder();
    const stderrDecoder = new TextDecoder();

    pyodide.setStdout({
      write: (buf: Uint8Array) => {
        stdoutBuffer += stdoutDecoder.decode(buf, { stream: true });
        return buf.length;
      }
    });
    pyodide.setStderr({
      write: (buf: Uint8Array) => {
        stderrBuffer += stderrDecoder.decode(buf, { stream: true });
        return buf.length;
      }
    });

    // Load session state if provided
    if (options.stateful && options.sessionBytes) {
      try {
        // Temporarily suppress stdout during cloudpickle installation to avoid polluting JSON output
        const originalStdout = stdoutBuffer;
        pyodide.setStdout({ write: (buf: Uint8Array) => buf.length }); // Suppress micropip output

        await pyodide.runPythonAsync(`
# Install cloudpickle if needed
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);

        // Restore stdout capture
        pyodide.setStdout({
          write: (buf: Uint8Array) => {
            stdoutBuffer += stdoutDecoder.decode(buf, { stream: true });
            return buf.length;
          }
        });

        // Now restore the session
        await pyodide.runPythonAsync(`
_session_bytes = bytes(${JSON.stringify(Array.from(options.sessionBytes))})
_session_obj = cloudpickle.loads(_session_bytes)
globals().update(_session_obj)
`);
      } catch (e) {
        stderrBuffer += `Session restore error: ${e}\n`;
        // Restore stdout capture in case of error
        pyodide.setStdout({
          write: (buf: Uint8Array) => {
            stdoutBuffer += stdoutDecoder.decode(buf, { stream: true });
            return buf.length;
          }
        });
      }
    }

    // Mount files if provided
    if (options.files) {
      for (const [path, content] of Object.entries(options.files)) {
        const dir = path.substring(0, path.lastIndexOf("/"));
        if (dir && dir !== "/") {
          pyodide.FS.mkdirTree(dir);
        }
        pyodide.FS.writeFile(path, content);
      }

      // Invalidate Python import cache so new modules are recognized
      await pyodide.runPythonAsync(`
import importlib
importlib.invalidate_caches()
`);
    }

    // Pre-configure environment for common packages
    // Set matplotlib backend to Agg for Deno/Node.js compatibility
    // See: https://github.com/pyodide/matplotlib-pyodide/issues/36
    try {
      await pyodide.runPythonAsync(`
import os
import sys

# Set Agg backend for matplotlib (required for Deno/Node.js/non-browser contexts)
if 'matplotlib' not in sys.modules:
    os.environ['MPLBACKEND'] = 'Agg'
`);
    } catch (e) {
      // Setup failed, continue anyway (matplotlib might not be used)
    }

    // Track file operations during execution using FS.trackingDelegate
    const createdFiles = new Set<string>();
    const modifiedFiles = new Set<string>();

    // Install tracking delegate before execution
    pyodide.FS.trackingDelegate = {
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
      const execResult = await pyodide.runPythonAsync(options.code);
      result.result = execResult;
      result.success = true;
    } catch (e) {
      stderrBuffer += `${e}\n`;
      result.success = false;
    }

    // Remove tracking delegate
    pyodide.FS.trackingDelegate = {};

    // Filter out micropip loading messages to keep output clean
    result.stdout = filterMicropipMessages(stdoutBuffer);
    result.stderr = stderrBuffer;

    // Save session state if stateful
    if (options.stateful && result.success) {
      try {
        // Temporarily suppress stdout during cloudpickle installation to avoid polluting JSON output
        pyodide.setStdout({ write: (buf: Uint8Array) => buf.length }); // Suppress micropip output

        await pyodide.runPythonAsync(`
# Install cloudpickle if needed for session serialization
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);

        // Restore stdout capture (but don't append since we're done with user code)
        pyodide.setStdout({
          write: (buf: Uint8Array) => {
            stdoutBuffer += stdoutDecoder.decode(buf, { stream: true });
            return buf.length;
          }
        });

        // Now serialize the session
        const sessionBytesResult = await pyodide.runPythonAsync(`
# Filter out unpicklable objects (file handles, built-in types, etc.)
import types
_globals_snapshot = dict(globals())  # Snapshot to avoid "changed size during iteration"
_session_dict = {}
for k, v in _globals_snapshot.items():
    if k.startswith('_'):
        continue
    # Skip built-in types (classes like int, str, etc.)
    if isinstance(v, type) and v.__module__ == 'builtins':
        continue
    # Skip file-like objects (closed file handles cause pickle errors)
    if hasattr(v, 'read') or hasattr(v, 'write'):
        continue
    # cloudpickle can handle modules by reference, so include them
    _session_dict[k] = v

list(cloudpickle.dumps(_session_dict))
`);
        result.sessionBytes = sessionBytesResult.toJs();
        result.sessionMetadata = {
          ...options.sessionMetadata,
          lastModified: new Date().toISOString(),
        };
      } catch (e) {
        stderrBuffer += `Session save error: ${e}\n`;
        result.stderr = stderrBuffer;
        // Restore stdout capture in case of error
        pyodide.setStdout({
          write: (buf: Uint8Array) => {
            stdoutBuffer += stdoutDecoder.decode(buf, { stream: true });
            return buf.length;
          }
        });
      }
    }

    // Collect all tracked files (created OR modified) with contents for VFS persistence
    const allChangedPaths = new Set([...createdFiles, ...modifiedFiles]);
    result.files = collectFilesFromPaths(pyodide, Array.from(allChangedPaths));

    return result;
  } catch (e) {
    result.stderr += `Execution error: ${e}\n`;
    return result;
  }
}

/**
 * Main entry point
 */
async function main() {
  const args = parseArgs(Deno.args, {
    string: ["code", "session-bytes", "session-metadata"],
    boolean: ["stateful"],
    alias: {
      c: "code",
      s: "stateful",
      b: "session-bytes",
      m: "session-metadata",
    },
  });

  if (!args.code) {
    console.error("Usage: executor.ts -c <code> [-s] [-b <session-bytes>]");
    Deno.exit(1);
  }

  // Read files from stdin
  const files = await readStdinFiles();

  // Parse session data
  let sessionBytes: Uint8Array | undefined;
  let sessionMetadata: Record<string, unknown> | undefined;

  if (args["session-bytes"]) {
    sessionBytes = new Uint8Array(JSON.parse(args["session-bytes"]));
  }

  if (args["session-metadata"]) {
    sessionMetadata = JSON.parse(args["session-metadata"]);
  }

  // Execute
  const result = await execute({
    code: args.code,
    files,
    stateful: args.stateful || false,
    sessionBytes,
    sessionMetadata,
  });

  // Output result as JSON
  console.log(JSON.stringify(result));
}

if (import.meta.main) {
  main();
}
