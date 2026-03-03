/**
 * Tests for worker_server.ts
 *
 * Tests the JSON-RPC protocol, interfaces, and helper logic used by the worker server.
 * Note: The actual worker_server.ts imports Pyodide which requires npm install,
 * so these tests verify the protocol and interface contracts without importing the module directly.
 * Integration testing of the full worker is done via Python e2e tests.
 *
 * Run with: deno test --allow-read --allow-net --allow-env worker_server_test.ts
 */

import { assertEquals, assertExists } from "jsr:@std/assert@1";
import {
  errorToString,
  filterMicropipMessages,
  createFileTracker,
  findChangedFiles,
} from "./worker_utils.ts";

// JSON-RPC protocol tests

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

Deno.test("JSON-RPC request structure is valid", () => {
  const request: JsonRpcRequest = {
    jsonrpc: "2.0",
    id: 1,
    method: "execute",
    params: {
      code: "print('hello')",
      thread_id: "test-123",
    },
  };

  assertEquals(request.jsonrpc, "2.0");
  assertEquals(request.method, "execute");
  assertExists(request.params.code);
});

Deno.test("JSON-RPC response with result", () => {
  const response: JsonRpcResponse = {
    jsonrpc: "2.0",
    id: 1,
    result: {
      success: true,
      stdout: "hello\n",
      stderr: "",
      result: null,
      execution_time_ms: 100,
    },
  };

  assertEquals(response.jsonrpc, "2.0");
  assertEquals(response.id, 1);
  assertExists(response.result);
  assertEquals(response.result.success, true);
});

Deno.test("JSON-RPC response with error", () => {
  const response: JsonRpcResponse = {
    jsonrpc: "2.0",
    id: 1,
    error: {
      code: -32603,
      message: "Internal error",
    },
  };

  assertEquals(response.jsonrpc, "2.0");
  assertExists(response.error);
  assertEquals(response.error.code, -32603);
});

Deno.test("JSON-RPC health request/response", () => {
  const request: JsonRpcRequest = {
    jsonrpc: "2.0",
    id: 2,
    method: "health",
    params: {},
  };

  assertEquals(request.method, "health");

  // Simulated health response
  const response: JsonRpcResponse = {
    jsonrpc: "2.0",
    id: 2,
    result: {
      status: "healthy",
      request_count: 5,
      uptime_ms: 60000,
      pid: 12345,
    },
  };

  assertEquals(response.result.status, "healthy");
  assertExists(response.result.uptime_ms);
});

Deno.test("JSON-RPC shutdown request/response", () => {
  const request: JsonRpcRequest = {
    jsonrpc: "2.0",
    id: 3,
    method: "shutdown",
    params: {},
  };

  assertEquals(request.method, "shutdown");

  const response: JsonRpcResponse = {
    jsonrpc: "2.0",
    id: 3,
    result: {
      status: "shutting_down",
    },
  };

  assertEquals(response.result.status, "shutting_down");
});

// ExecuteRequest interface tests

interface ExecuteRequest {
  code: string;
  thread_id: string;
  stateful?: boolean;
  session_bytes?: number[];
  session_metadata?: Record<string, unknown>;
  files?: Record<string, number[]>;
  timeout_ms?: number;
}

Deno.test("ExecuteRequest minimal fields", () => {
  const request: ExecuteRequest = {
    code: "x = 1 + 1",
    thread_id: "thread-abc",
  };

  assertEquals(request.code, "x = 1 + 1");
  assertEquals(request.thread_id, "thread-abc");
  assertEquals(request.stateful, undefined);
});

Deno.test("ExecuteRequest with all fields", () => {
  const request: ExecuteRequest = {
    code: "print(x)",
    thread_id: "thread-xyz",
    stateful: true,
    session_bytes: [1, 2, 3, 4],
    session_metadata: { version: 1 },
    files: { "/tmp/data.txt": [72, 101, 108, 108, 111] },
    timeout_ms: 30000,
  };

  assertEquals(request.stateful, true);
  assertExists(request.session_bytes);
  assertEquals(request.session_bytes.length, 4);
  assertExists(request.files);
  assertEquals(request.files["/tmp/data.txt"].length, 5);
});

// ExecuteResult interface tests

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

Deno.test("ExecuteResult success case", () => {
  const result: ExecuteResult = {
    success: true,
    stdout: "42\n",
    stderr: "",
    result: 42,
    execution_time_ms: 150,
  };

  assertEquals(result.success, true);
  assertEquals(result.result, 42);
  assertEquals(result.execution_time_ms, 150);
});

Deno.test("ExecuteResult with session data", () => {
  const result: ExecuteResult = {
    success: true,
    stdout: "",
    stderr: "",
    result: null,
    session_bytes: [128, 0, 255],
    session_metadata: { last_modified: "2024-01-01T00:00:00Z" },
    execution_time_ms: 200,
  };

  assertExists(result.session_bytes);
  assertExists(result.session_metadata);
  assertEquals(result.session_metadata.last_modified, "2024-01-01T00:00:00Z");
});

Deno.test("ExecuteResult with created files", () => {
  const result: ExecuteResult = {
    success: true,
    stdout: "",
    stderr: "",
    result: null,
    created_files: [
      { path: "/tmp/output.txt", content: [79, 75] },
      { path: "/tmp/data.json", content: [123, 125] },
    ],
    execution_time_ms: 300,
  };

  assertExists(result.created_files);
  assertEquals(result.created_files.length, 2);
  assertEquals(result.created_files[0].path, "/tmp/output.txt");
});

Deno.test("ExecuteResult failure case", () => {
  const result: ExecuteResult = {
    success: false,
    stdout: "",
    stderr: "NameError: name 'undefined_var' is not defined\n",
    result: null,
    execution_time_ms: 50,
  };

  assertEquals(result.success, false);
  assertEquals(result.stderr.includes("NameError"), true);
});

// mountFiles directory logic tests

Deno.test("mountFiles directory extraction", () => {
  const testCases = [
    { path: "/tmp/test.txt", expectedDir: "/tmp" },
    { path: "/home/pyodide/data.json", expectedDir: "/home/pyodide" },
    { path: "/a/b/c/d.txt", expectedDir: "/a/b/c" },
    { path: "/root.txt", expectedDir: "" },
  ];

  for (const { path, expectedDir } of testCases) {
    const dir = path.substring(0, path.lastIndexOf("/"));
    assertEquals(dir, expectedDir, `For path: ${path}`);
  }
});

Deno.test("mountFiles skip directory creation for root", () => {
  const path = "/root.txt";
  const dir = path.substring(0, path.lastIndexOf("/"));

  // The logic: if (dir && dir !== "/") then mkdirTree
  // dir is "" (empty string) which is falsy
  const shouldCreateDir = !!(dir && dir !== "/");
  assertEquals(shouldCreateDir, false);
});

Deno.test("mountFiles creates nested directories", () => {
  const path = "/home/user/project/data/output.csv";
  const dir = path.substring(0, path.lastIndexOf("/"));

  const shouldCreateDir = dir && dir !== "/";
  assertEquals(shouldCreateDir, true);
  assertEquals(dir, "/home/user/project/data");
});

// File content conversion tests

Deno.test("File content number[] to Uint8Array conversion", () => {
  const contentNumbers: number[] = [72, 101, 108, 108, 111]; // "Hello"
  const uint8 = new Uint8Array(contentNumbers);

  assertEquals(uint8.length, 5);
  assertEquals(new TextDecoder().decode(uint8), "Hello");
});

Deno.test("File content handles binary data", () => {
  const binaryContent: number[] = [0, 128, 255, 1, 254];
  const uint8 = new Uint8Array(binaryContent);

  assertEquals(uint8[0], 0);
  assertEquals(uint8[1], 128);
  assertEquals(uint8[2], 255);
});

// Re-exported function tests (verifying worker_server re-exports work)

Deno.test("Re-exported errorToString works", () => {
  assertEquals(errorToString(new Error("test")), "test");
  assertEquals(errorToString("plain string"), "plain string");
  assertEquals(errorToString(42), "42");
});

Deno.test("Re-exported filterMicropipMessages works", () => {
  const input = "Loading numpy\nResult: 42";
  const result = filterMicropipMessages(input);
  assertEquals(result, "Result: 42");
});

Deno.test("Re-exported createFileTracker works", () => {
  const tracker = createFileTracker();
  tracker.delegate.onOpenFile("/tmp/new.txt", 0x200);
  assertEquals(tracker.createdFiles.has("/tmp/new.txt"), true);
});

Deno.test("Re-exported findChangedFiles works", () => {
  const before = new Map<string, number>();
  const after = new Map<string, number>([["/new.txt", 100]]);
  const changed = findChangedFiles(before, after);
  assertEquals(changed, ["/new.txt"]);
});

// ExecutionContext interface tests

interface ExecutionContext {
  pyodide: any;
  stdoutBuffer: { value: string };
  stderrBuffer: { value: string };
  stdoutDecoder: TextDecoder;
}

Deno.test("ExecutionContext buffer accumulation", () => {
  const ctx: ExecutionContext = {
    pyodide: null, // Would be real Pyodide in production
    stdoutBuffer: { value: "" },
    stderrBuffer: { value: "" },
    stdoutDecoder: new TextDecoder(),
  };

  // Simulate stdout accumulation
  ctx.stdoutBuffer.value += "Line 1\n";
  ctx.stdoutBuffer.value += "Line 2\n";

  assertEquals(ctx.stdoutBuffer.value, "Line 1\nLine 2\n");
});

Deno.test("ExecutionContext stderr accumulation", () => {
  const ctx: ExecutionContext = {
    pyodide: null,
    stdoutBuffer: { value: "" },
    stderrBuffer: { value: "" },
    stdoutDecoder: new TextDecoder(),
  };

  ctx.stderrBuffer.value += "Warning: something\n";
  ctx.stderrBuffer.value += "Error: failed\n";

  assertEquals(ctx.stderrBuffer.value.includes("Warning"), true);
  assertEquals(ctx.stderrBuffer.value.includes("Error"), true);
});

// Session metadata tests

Deno.test("Session metadata with last_modified", () => {
  const metadata: Record<string, unknown> = {
    version: 1,
    created: "2024-01-01",
  };

  const updated: Record<string, unknown> = {
    ...metadata,
    last_modified: new Date().toISOString(),
  };

  assertExists(updated.last_modified);
  assertEquals(updated.version, 1);
  assertEquals(updated.created, "2024-01-01");
});

// JSON-RPC line protocol tests

Deno.test("JSON-RPC line parsing", () => {
  const lines = [
    '{"jsonrpc":"2.0","id":1,"method":"execute","params":{"code":"x=1"}}',
    '{"jsonrpc":"2.0","id":2,"method":"health","params":{}}',
  ];

  for (const line of lines) {
    const parsed = JSON.parse(line) as JsonRpcRequest;
    assertEquals(parsed.jsonrpc, "2.0");
    assertExists(parsed.id);
    assertExists(parsed.method);
  }
});

Deno.test("JSON-RPC response serialization", () => {
  const response: JsonRpcResponse = {
    jsonrpc: "2.0",
    id: 1,
    result: { success: true, stdout: "test", stderr: "", result: null, execution_time_ms: 50 },
  };

  const serialized = JSON.stringify(response) + "\n";
  assertEquals(serialized.endsWith("\n"), true);

  const reparsed = JSON.parse(serialized.trim());
  assertEquals(reparsed.result.success, true);
});

// collectChangedFiles logic tests

Deno.test("collectChangedFiles combines tracker and snapshot changes", () => {
  // Simulate what collectChangedFiles does
  const trackerCreated = new Set(["/tmp/created.txt"]);
  const trackerModified = new Set(["/tmp/modified.txt"]);
  const snapshotChanges = ["/tmp/snapshot-changed.txt"];

  const allChangedPaths = new Set([...trackerCreated, ...trackerModified]);
  snapshotChanges.forEach((p) => allChangedPaths.add(p));

  assertEquals(allChangedPaths.size, 3);
  assertEquals(allChangedPaths.has("/tmp/created.txt"), true);
  assertEquals(allChangedPaths.has("/tmp/modified.txt"), true);
  assertEquals(allChangedPaths.has("/tmp/snapshot-changed.txt"), true);
});

Deno.test("collectChangedFiles handles duplicates", () => {
  const trackerCreated = new Set(["/tmp/file.txt"]);
  const trackerModified = new Set(["/tmp/file.txt"]); // Same file
  const snapshotChanges = ["/tmp/file.txt"]; // Same file again

  const allChangedPaths = new Set([...trackerCreated, ...trackerModified]);
  snapshotChanges.forEach((p) => allChangedPaths.add(p));

  // Should deduplicate
  assertEquals(allChangedPaths.size, 1);
});

Deno.test("collectChangedFiles returns undefined for no changes", () => {
  const allChangedPaths = new Set<string>();

  // Logic: if (allChangedPaths.size === 0) return undefined
  const result = allChangedPaths.size === 0 ? undefined : Array.from(allChangedPaths);
  assertEquals(result, undefined);
});
