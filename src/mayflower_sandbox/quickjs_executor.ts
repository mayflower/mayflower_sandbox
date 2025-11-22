/**
 * Mayflower Sandbox - QuickJS Executor
 *
 * Minimal QuickJS-Wasm executor with stdin/stdout protocol.
 * Executes JavaScript/TypeScript in a sandboxed WebAssembly environment.
 */

import { newQuickJSWASMModule } from "npm:quickjs-emscripten@0.29.2";
import { parseArgs } from "jsr:@std/cli@1.0.23/parse-args";
import * as esbuild from "https://deno.land/x/esbuild@v0.24.0/mod.js";

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
 * Read binary file data from stdin using MFS protocol
 * Same format as Pyodide executor for consistency
 */
async function readStdinFiles(): Promise<Record<string, Uint8Array>> {
  const files: Record<string, Uint8Array> = {};

  // Read all stdin data
  const chunks: Uint8Array[] = [];
  const buffer = new Uint8Array(8192);

  while (true) {
    const bytesRead = await Deno.stdin.read(buffer);
    if (bytesRead === null) break;
    chunks.push(buffer.slice(0, bytesRead));
  }

  const stdinData = new Uint8Array(
    chunks.reduce((acc, chunk) => acc + chunk.length, 0)
  );
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
 * Detect if code is TypeScript by checking for TS-specific syntax
 */
function isTypeScript(code: string): boolean {
  // Check for TypeScript-specific syntax
  const tsPatterns = [
    /:\s*(string|number|boolean|any|unknown|void|never)\b/,  // Type annotations
    /\binterface\s+\w+/,                                      // Interface declarations
    /\btype\s+\w+\s*=/,                                       // Type aliases
    /\benum\s+\w+/,                                           // Enums
    /<\w+>/,                                                  // Generic type parameters
    /\bas\s+\w+/,                                             // Type assertions
    /:\s*\w+\[\]/,                                            // Array type annotations
  ];

  return tsPatterns.some((pattern) => pattern.test(code));
}

/**
 * Transpile TypeScript to JavaScript using esbuild
 */
async function transpileTypeScript(code: string): Promise<string> {
  try {
    const result = await esbuild.transform(code, {
      loader: "ts",
      target: "es2020",
    });
    return result.code;
  } catch (error) {
    throw new Error(`TypeScript transpilation failed: ${error}`);
  }
}

/**
 * Execute JavaScript code in QuickJS-Wasm sandbox
 */
async function execute(options: ExecutionOptions): Promise<ExecutionResult> {
  const result: ExecutionResult = {
    success: false,
    stdout: "",
    stderr: "",
    result: null,
  };

  try {
    // Transpile TypeScript to JavaScript if needed
    let code = options.code;
    if (isTypeScript(code)) {
      try {
        code = await transpileTypeScript(code);
      } catch (error) {
        result.stderr = `TypeScript transpilation error: ${error}\n`;
        result.success = false;
        return result;
      }
    }

    // Load QuickJS WebAssembly module
    const QuickJS = await newQuickJSWASMModule();
    const vm = QuickJS.newContext();

    try {
      // Capture stdout/stderr
      let stdoutBuffer = "";
      let stderrBuffer = "";

      // Create virtual filesystem in memory
      const vfs = new Map<string, Uint8Array>();
      const modifiedFiles = new Set<string>();

      // Mount files from VFS
      if (options.files) {
        for (const [path, content] of Object.entries(options.files)) {
          vfs.set(path, content);
        }
      }

      // Inject console.log/error handlers
      const consoleLog = vm.newFunction("log", (...args) => {
        const message = args
          .map((handle) => {
            const value = vm.dump(handle);
            return typeof value === "string" ? value : JSON.stringify(value);
          })
          .join(" ");
        stdoutBuffer += message + "\n";
      });

      const consoleError = vm.newFunction("error", (...args) => {
        const message = args
          .map((handle) => {
            const value = vm.dump(handle);
            return typeof value === "string" ? value : JSON.stringify(value);
          })
          .join(" ");
        stderrBuffer += message + "\n";
      });

      // Create console object
      const consoleObj = vm.newObject();
      vm.setProp(consoleObj, "log", consoleLog);
      vm.setProp(consoleObj, "error", consoleError);
      vm.setProp(vm.global, "console", consoleObj);

      // Inject readFile function
      const readFileHandle = vm.newFunction("readFile", (pathHandle) => {
        const path = vm.getString(pathHandle);
        const content = vfs.get(path);

        if (!content) {
          throw vm.newError(`File not found: ${path}`);
        }

        // Try to decode as UTF-8, fallback to base64 for binary
        try {
          const text = new TextDecoder().decode(content);
          return vm.newString(text);
        } catch {
          // Binary file - return base64
          const base64 = btoa(String.fromCharCode(...content));
          return vm.newString(base64);
        }
      });

      // Inject writeFile function
      const writeFileHandle = vm.newFunction(
        "writeFile",
        (pathHandle, contentHandle) => {
          const path = vm.getString(pathHandle);
          const content = vm.getString(contentHandle);

          // Encode string to bytes
          const bytes = new TextEncoder().encode(content);
          vfs.set(path, bytes);
          modifiedFiles.add(path);

          return vm.undefined;
        }
      );

      // Inject listFiles function
      const listFilesHandle = vm.newFunction("listFiles", () => {
        const paths = Array.from(vfs.keys());
        const arrayHandle = vm.newArray();
        paths.forEach((path, index) => {
          vm.setProp(arrayHandle, index, vm.newString(path));
        });
        return arrayHandle;
      });

      // Set global file I/O functions
      vm.setProp(vm.global, "readFile", readFileHandle);
      vm.setProp(vm.global, "writeFile", writeFileHandle);
      vm.setProp(vm.global, "listFiles", listFilesHandle);

      // Execute code (transpiled if TypeScript)
      const execResult = vm.evalCode(code);

      if (execResult.error) {
        // Execution error - format properly
        const errorObj = vm.dump(execResult.error);
        let errorMessage: string;

        if (typeof errorObj === "object" && errorObj !== null) {
          // If it's an Error object, format it nicely
          if (errorObj.message) {
            errorMessage = errorObj.name
              ? `${errorObj.name}: ${errorObj.message}`
              : errorObj.message;
            if (errorObj.stack) {
              errorMessage += `\n${errorObj.stack}`;
            }
          } else {
            // Fallback to JSON stringify
            errorMessage = JSON.stringify(errorObj);
          }
        } else {
          errorMessage = String(errorObj);
        }

        stderrBuffer += `${errorMessage}\n`;
        result.success = false;
        execResult.error.dispose();
      } else {
        // Success - get result value
        result.result = vm.dump(execResult.value);
        result.success = true;
        execResult.value.dispose();
      }

      result.stdout = stdoutBuffer;
      result.stderr = stderrBuffer;

      // Collect modified files
      if (modifiedFiles.size > 0) {
        result.files = Array.from(modifiedFiles).map((path) => ({
          path,
          content: Array.from(vfs.get(path)!),
        }));
      }

      // Session state not yet implemented for QuickJS
      // (no equivalent to cloudpickle for JavaScript)
      if (options.stateful) {
        result.stderr += "Warning: Stateful execution not yet supported for JavaScript\n";
      }

      // Cleanup handles
      consoleLog.dispose();
      consoleError.dispose();
      consoleObj.dispose();
      readFileHandle.dispose();
      writeFileHandle.dispose();
      listFilesHandle.dispose();
    } finally {
      // Always dispose VM
      vm.dispose();
    }

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
    console.error(
      "Usage: quickjs_executor.ts -c <code> [-s] [-b <session-bytes>]"
    );
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

  // Stop esbuild to free resources
  esbuild.stop();
}

if (import.meta.main) {
  main();
}
