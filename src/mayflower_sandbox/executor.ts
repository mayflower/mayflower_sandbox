/**
 * Mayflower Sandbox - Pyodide Executor
 *
 * Clean, minimal Pyodide executor with stdin/stdout protocol.
 * No dependency on langchain-sandbox - written from scratch.
 */

import { loadPyodide } from "npm:pyodide@0.28.3";
import { parseArgs } from "jsr:@std/cli@1.0.23/parse-args";
import { collectFilesFromPaths } from "./fs_utils.ts";

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

// File operations (snapshotFiles, collectFiles, collectFilesFromPaths)
// are now in fs_utils.ts to reduce duplication and cognitive complexity

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
 * Create suppressed stdout handler (discards output)
 */
function createSuppressedStdout(): { write: (buf: Uint8Array) => number } {
  return { write: (buf: Uint8Array) => buf.length };
}

/**
 * Restore session state from bytes
 */
async function restoreSession(
  pyodide: any,
  sessionBytes: Uint8Array,
  stdoutBuffer: { value: string },
  stderrBuffer: { value: string },
  stdoutDecoder: TextDecoder,
): Promise<void> {
  pyodide.setStdout(createSuppressedStdout());

  await pyodide.runPythonAsync(`
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);

  pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));

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
async function mountFiles(
  pyodide: any,
  files: Record<string, Uint8Array>,
): Promise<void> {
  for (const [path, content] of Object.entries(files)) {
    const dir = path.substring(0, path.lastIndexOf("/"));
    if (dir && dir !== "/") {
      pyodide.FS.mkdirTree(dir);
    }
    pyodide.FS.writeFile(path, content);
  }

  await pyodide.runPythonAsync(`
import importlib
importlib.invalidate_caches()
`);
}

/**
 * Configure matplotlib backend for non-browser environments
 */
async function configureEnvironment(pyodide: any): Promise<void> {
  try {
    await pyodide.runPythonAsync(`
import os
import sys
if 'matplotlib' not in sys.modules:
    os.environ['MPLBACKEND'] = 'Agg'
`);
  } catch {
    // matplotlib config failed - not critical, continue anyway
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
    const pyodide = await loadPyodide();
    await pyodide.loadPackage("micropip");

    const stdoutBuffer = { value: "" };
    const stderrBuffer = { value: "" };
    const stdoutDecoder = new TextDecoder();
    const stderrDecoder = new TextDecoder();

    pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
    pyodide.setStderr(createStdoutHandler(stderrBuffer, stderrDecoder));

    // Restore session if stateful
    if (options.stateful && options.sessionBytes) {
      try {
        await restoreSession(pyodide, options.sessionBytes, stdoutBuffer, stderrBuffer, stdoutDecoder);
      } catch (e) {
        stderrBuffer.value += `Session restore error: ${e}\n`;
        pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
      }
    }

    // Mount files
    if (options.files) {
      await mountFiles(pyodide, options.files);
    }

    await configureEnvironment(pyodide);

    // Track file operations
    const tracker = createFileTracker();
    pyodide.FS.trackingDelegate = tracker.delegate;

    // Execute code
    try {
      result.result = await pyodide.runPythonAsync(options.code);
      result.success = true;
    } catch (e) {
      stderrBuffer.value += `${e}\n`;
    }

    pyodide.FS.trackingDelegate = {};

    result.stdout = filterMicropipMessages(stdoutBuffer.value);
    result.stderr = stderrBuffer.value;

    // Save session if stateful and successful
    if (options.stateful && result.success) {
      try {
        pyodide.setStdout(createSuppressedStdout());
        await pyodide.runPythonAsync(`
try:
    import cloudpickle
except ImportError:
    import micropip
    await micropip.install('cloudpickle')
    import cloudpickle
`);
        pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));

        result.sessionBytes = await saveSession(pyodide);
        result.sessionMetadata = {
          ...options.sessionMetadata,
          lastModified: new Date().toISOString(),
        };
      } catch (e) {
        stderrBuffer.value += `Session save error: ${e}\n`;
        result.stderr = stderrBuffer.value;
        pyodide.setStdout(createStdoutHandler(stdoutBuffer, stdoutDecoder));
      }
    }

    // Collect changed files
    const allChangedPaths = new Set([...tracker.createdFiles, ...tracker.modifiedFiles]);
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
