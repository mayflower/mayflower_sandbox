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

    // Load micropip if stateful (needed for dill)
    if (options.stateful) {
      await pyodide.loadPackage("micropip");
    }

    // Capture stdout/stderr
    let stdoutBuffer = "";
    let stderrBuffer = "";

    pyodide.setStdout({ batched: (text: string) => { stdoutBuffer += text; } });
    pyodide.setStderr({ batched: (text: string) => { stderrBuffer += text; } });

    // Load session state if provided
    if (options.stateful && options.sessionBytes) {
      try {
        await pyodide.runPythonAsync(`
# Install dill if needed
try:
    import dill
except ImportError:
    import micropip
    await micropip.install('dill')
    import dill

_session_bytes = bytes(${JSON.stringify(Array.from(options.sessionBytes))})
_session_obj = dill.loads(_session_bytes)
globals().update(_session_obj)
`);
      } catch (e) {
        stderrBuffer += `Session restore error: ${e}\n`;
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

    // Execute code
    try {
      const execResult = await pyodide.runPythonAsync(options.code);
      result.result = execResult;
      result.success = true;
    } catch (e) {
      stderrBuffer += `${e}\n`;
      result.success = false;
    }

    result.stdout = stdoutBuffer;
    result.stderr = stderrBuffer;

    // Save session state if stateful
    if (options.stateful && result.success) {
      try {
        const sessionBytesResult = await pyodide.runPythonAsync(`
# Install dill if needed for session serialization
try:
    import dill
except ImportError:
    import micropip
    await micropip.install('dill')
    import dill

_session_dict = {k: v for k, v in globals().items() if not k.startswith('_')}
list(dill.dumps(_session_dict))
`);
        result.sessionBytes = sessionBytesResult.toJs();
        result.sessionMetadata = {
          ...options.sessionMetadata,
          lastModified: new Date().toISOString(),
        };
      } catch (e) {
        stderrBuffer += `Session save error: ${e}\n`;
        result.stderr = stderrBuffer;
      }
    }

    // Collect created files
    result.files = collectFiles(pyodide, ["/tmp", "/data"]);

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
