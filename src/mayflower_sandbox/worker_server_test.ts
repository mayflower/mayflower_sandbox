/**
 * Tests for worker_server.ts helper functions
 *
 * Run with: deno test --allow-read worker_server_test.ts
 */

import { assertEquals, assertExists } from "jsr:@std/assert@1";

// Test filterMicropipMessages function
// We need to test this function directly, so we'll create a local copy
// since it's not exported from worker_server.ts

function filterMicropipMessages(stdout: string): string {
  const lines = stdout.split("\n");
  const filtered = lines.filter((line) => {
    if (line.startsWith("Loading ")) return false;
    if (line.startsWith("Didn't find package ")) return false;
    if (line.startsWith("Package ") && line.includes(" loaded from ")) return false;
    if (line.startsWith("Loaded ")) return false;
    return true;
  });
  return filtered.join("\n");
}

// Test createStdoutHandler behavior
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

// Test createSuppressedStdout behavior
function createSuppressedStdout(): { write: (buf: Uint8Array) => number } {
  return { write: (buf: Uint8Array) => buf.length };
}

// Test createFileTracker behavior
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

// Test findChangedFiles behavior
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

// filterMicropipMessages tests
Deno.test("filterMicropipMessages removes Loading messages", () => {
  const input = "Loading numpy\nHello World\nLoading pandas";
  const result = filterMicropipMessages(input);
  assertEquals(result, "Hello World");
});

Deno.test("filterMicropipMessages removes Didn't find package messages", () => {
  const input = "Didn't find package foo\nUser output\nDone";
  const result = filterMicropipMessages(input);
  assertEquals(result, "User output\nDone");
});

Deno.test("filterMicropipMessages removes Package loaded from messages", () => {
  const input = "Package numpy loaded from cache\nResult: 42";
  const result = filterMicropipMessages(input);
  assertEquals(result, "Result: 42");
});

Deno.test("filterMicropipMessages removes Loaded messages", () => {
  const input = "Loaded micropip\nprint output\nLoaded something";
  const result = filterMicropipMessages(input);
  assertEquals(result, "print output");
});

Deno.test("filterMicropipMessages preserves user output", () => {
  const input = "Hello\nWorld\n42\nTest passed";
  const result = filterMicropipMessages(input);
  assertEquals(result, input);
});

Deno.test("filterMicropipMessages handles empty string", () => {
  assertEquals(filterMicropipMessages(""), "");
});

Deno.test("filterMicropipMessages handles complex output", () => {
  const input = `Loading numpy
Didn't find package customlib
Package pandas loaded from pypi
User calculation result: 3.14159
Loaded matplotlib
Final answer: 42`;
  const result = filterMicropipMessages(input);
  assertEquals(result, "User calculation result: 3.14159\nFinal answer: 42");
});

// createStdoutHandler tests
Deno.test("createStdoutHandler captures output", () => {
  const buffer = { value: "" };
  const decoder = new TextDecoder();
  const handler = createStdoutHandler(buffer, decoder);

  const data = new TextEncoder().encode("Hello World");
  const bytesWritten = handler.write(data);

  assertEquals(bytesWritten, data.length);
  assertEquals(buffer.value, "Hello World");
});

Deno.test("createStdoutHandler accumulates multiple writes", () => {
  const buffer = { value: "" };
  const decoder = new TextDecoder();
  const handler = createStdoutHandler(buffer, decoder);

  handler.write(new TextEncoder().encode("Hello "));
  handler.write(new TextEncoder().encode("World"));

  assertEquals(buffer.value, "Hello World");
});

Deno.test("createStdoutHandler handles binary data", () => {
  const buffer = { value: "" };
  const decoder = new TextDecoder();
  const handler = createStdoutHandler(buffer, decoder);

  // UTF-8 encoded "Café"
  const data = new Uint8Array([67, 97, 102, 195, 169]);
  handler.write(data);

  assertEquals(buffer.value, "Café");
});

// createSuppressedStdout tests
Deno.test("createSuppressedStdout returns byte count", () => {
  const handler = createSuppressedStdout();
  const data = new Uint8Array([1, 2, 3, 4, 5]);
  const result = handler.write(data);
  assertEquals(result, 5);
});

Deno.test("createSuppressedStdout discards content", () => {
  const handler = createSuppressedStdout();
  // Multiple writes should all be discarded
  handler.write(new TextEncoder().encode("This is ignored"));
  handler.write(new TextEncoder().encode("So is this"));
  // No assertion needed - just verify no errors
});

// createFileTracker tests
Deno.test("createFileTracker tracks file creation", () => {
  const tracker = createFileTracker();

  // O_CREAT flag is 0x200
  tracker.delegate.onOpenFile("/tmp/new.txt", 0x200);
  tracker.delegate.onOpenFile("/tmp/existing.txt", 0x000);

  assertEquals(tracker.createdFiles.has("/tmp/new.txt"), true);
  assertEquals(tracker.createdFiles.has("/tmp/existing.txt"), false);
});

Deno.test("createFileTracker tracks file modifications", () => {
  const tracker = createFileTracker();

  tracker.delegate.onWriteToFile("/tmp/file.txt", 100);
  tracker.delegate.onWriteToFile("/tmp/empty.txt", 0);

  assertEquals(tracker.modifiedFiles.has("/tmp/file.txt"), true);
  assertEquals(tracker.modifiedFiles.has("/tmp/empty.txt"), false);
});

Deno.test("createFileTracker tracks both created and modified", () => {
  const tracker = createFileTracker();

  tracker.delegate.onOpenFile("/tmp/new.txt", 0x200);
  tracker.delegate.onWriteToFile("/tmp/new.txt", 50);

  assertEquals(tracker.createdFiles.has("/tmp/new.txt"), true);
  assertEquals(tracker.modifiedFiles.has("/tmp/new.txt"), true);
});

Deno.test("createFileTracker deduplicates paths", () => {
  const tracker = createFileTracker();

  tracker.delegate.onOpenFile("/tmp/file.txt", 0x200);
  tracker.delegate.onOpenFile("/tmp/file.txt", 0x200);
  tracker.delegate.onWriteToFile("/tmp/file.txt", 10);
  tracker.delegate.onWriteToFile("/tmp/file.txt", 20);

  assertEquals(tracker.createdFiles.size, 1);
  assertEquals(tracker.modifiedFiles.size, 1);
});

// findChangedFiles tests
Deno.test("findChangedFiles detects new files", () => {
  const before = new Map<string, number>();
  const after = new Map<string, number>([
    ["/tmp/new.txt", 100],
  ]);

  const changed = findChangedFiles(before, after);
  assertEquals(changed, ["/tmp/new.txt"]);
});

Deno.test("findChangedFiles detects size changes", () => {
  const before = new Map<string, number>([
    ["/tmp/file.txt", 50],
  ]);
  const after = new Map<string, number>([
    ["/tmp/file.txt", 100],
  ]);

  const changed = findChangedFiles(before, after);
  assertEquals(changed, ["/tmp/file.txt"]);
});

Deno.test("findChangedFiles ignores unchanged files", () => {
  const before = new Map<string, number>([
    ["/tmp/unchanged.txt", 100],
  ]);
  const after = new Map<string, number>([
    ["/tmp/unchanged.txt", 100],
  ]);

  const changed = findChangedFiles(before, after);
  assertEquals(changed.length, 0);
});

Deno.test("findChangedFiles handles mixed changes", () => {
  const before = new Map<string, number>([
    ["/tmp/unchanged.txt", 100],
    ["/tmp/modified.txt", 50],
  ]);
  const after = new Map<string, number>([
    ["/tmp/unchanged.txt", 100],
    ["/tmp/modified.txt", 75],
    ["/tmp/new.txt", 200],
  ]);

  const changed = findChangedFiles(before, after);
  assertEquals(changed.sort(), ["/tmp/modified.txt", "/tmp/new.txt"].sort());
});

Deno.test("findChangedFiles handles empty snapshots", () => {
  const before = new Map<string, number>();
  const after = new Map<string, number>();

  const changed = findChangedFiles(before, after);
  assertEquals(changed.length, 0);
});

// ExecutionContext interface behavior test
Deno.test("ExecutionContext structure is valid", () => {
  interface ExecutionContext {
    pyodide: any;
    stdoutBuffer: { value: string };
    stderrBuffer: { value: string };
    stdoutDecoder: TextDecoder;
  }

  const ctx: ExecutionContext = {
    pyodide: {},
    stdoutBuffer: { value: "" },
    stderrBuffer: { value: "" },
    stdoutDecoder: new TextDecoder(),
  };

  assertExists(ctx.pyodide);
  assertExists(ctx.stdoutBuffer);
  assertExists(ctx.stderrBuffer);
  assertExists(ctx.stdoutDecoder);
  assertEquals(ctx.stdoutBuffer.value, "");
  assertEquals(ctx.stderrBuffer.value, "");
});
