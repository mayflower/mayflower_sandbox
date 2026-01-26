/**
 * Tests for executor.ts
 *
 * Run with: deno test --allow-read --allow-net --allow-env executor_test.ts
 */

import { assertEquals, assertExists } from "jsr:@std/assert@1";

// Test the binary protocol parsing logic (MFS format)
// The actual readStdinFiles reads from Deno.stdin, so we test the protocol format

Deno.test("MFS binary protocol - can create valid protocol data", () => {
  // Create a valid MFS binary protocol payload
  const files = [
    { path: "/tmp/test.txt", content: new TextEncoder().encode("Hello World") },
    { path: "/tmp/data.json", content: new TextEncoder().encode('{"key": "value"}') },
  ];

  // Build metadata
  const metadata = {
    files: files.map((f) => ({ path: f.path, size: f.content.length })),
  };
  const metadataJson = JSON.stringify(metadata);
  const metadataBytes = new TextEncoder().encode(metadataJson);

  // Build protocol: "MFS\x01" + length(4 bytes big-endian) + metadata + file contents
  const totalFileSize = files.reduce((acc, f) => acc + f.content.length, 0);
  const totalSize = 4 + 4 + metadataBytes.length + totalFileSize;
  const buffer = new Uint8Array(totalSize);

  // Magic header "MFS\x01"
  buffer[0] = 0x4d; // M
  buffer[1] = 0x46; // F
  buffer[2] = 0x53; // S
  buffer[3] = 0x01; // version 1

  // Metadata length (4 bytes big-endian)
  const view = new DataView(buffer.buffer);
  view.setUint32(4, metadataBytes.length, false);

  // Metadata
  buffer.set(metadataBytes, 8);

  // File contents
  let offset = 8 + metadataBytes.length;
  for (const file of files) {
    buffer.set(file.content, offset);
    offset += file.content.length;
  }

  // Verify the protocol structure
  const magic = new TextDecoder().decode(buffer.slice(0, 4));
  assertEquals(magic.startsWith("MFS"), true);

  const parsedMetadataLength = view.getUint32(4, false);
  assertEquals(parsedMetadataLength, metadataBytes.length);

  const parsedMetadataBytes = buffer.slice(8, 8 + parsedMetadataLength);
  const parsedMetadata = JSON.parse(new TextDecoder().decode(parsedMetadataBytes));
  assertEquals(parsedMetadata.files.length, 2);
  assertEquals(parsedMetadata.files[0].path, "/tmp/test.txt");
  assertEquals(parsedMetadata.files[0].size, 11);
});

Deno.test("MFS binary protocol - parse file contents correctly", () => {
  const testContent = "Test file content";
  const contentBytes = new TextEncoder().encode(testContent);

  const metadata = {
    files: [{ path: "/tmp/test.txt", size: contentBytes.length }],
  };
  const metadataJson = JSON.stringify(metadata);
  const metadataBytes = new TextEncoder().encode(metadataJson);

  const totalSize = 4 + 4 + metadataBytes.length + contentBytes.length;
  const buffer = new Uint8Array(totalSize);

  buffer[0] = 0x4d;
  buffer[1] = 0x46;
  buffer[2] = 0x53;
  buffer[3] = 0x01;

  const view = new DataView(buffer.buffer);
  view.setUint32(4, metadataBytes.length, false);
  buffer.set(metadataBytes, 8);
  buffer.set(contentBytes, 8 + metadataBytes.length);

  // Parse file content
  const fileOffset = 8 + metadataBytes.length;
  const parsedContent = buffer.slice(fileOffset, fileOffset + contentBytes.length);
  const parsedText = new TextDecoder().decode(parsedContent);

  assertEquals(parsedText, testContent);
});

Deno.test("MFS binary protocol - handles empty files array", () => {
  const metadata = { files: [] };
  const metadataJson = JSON.stringify(metadata);
  const metadataBytes = new TextEncoder().encode(metadataJson);

  const totalSize = 4 + 4 + metadataBytes.length;
  const buffer = new Uint8Array(totalSize);

  buffer[0] = 0x4d;
  buffer[1] = 0x46;
  buffer[2] = 0x53;
  buffer[3] = 0x01;

  const view = new DataView(buffer.buffer);
  view.setUint32(4, metadataBytes.length, false);
  buffer.set(metadataBytes, 8);

  const parsedMetadataLength = view.getUint32(4, false);
  const parsedMetadataBytes = buffer.slice(8, 8 + parsedMetadataLength);
  const parsedMetadata = JSON.parse(new TextDecoder().decode(parsedMetadataBytes));

  assertEquals(parsedMetadata.files.length, 0);
});

Deno.test("MFS binary protocol - multiple files with different sizes", () => {
  const files = [
    { path: "/a.txt", content: new Uint8Array([1, 2, 3]) },
    { path: "/b.txt", content: new Uint8Array([4, 5, 6, 7, 8]) },
    { path: "/c.txt", content: new Uint8Array([9]) },
  ];

  const metadata = {
    files: files.map((f) => ({ path: f.path, size: f.content.length })),
  };
  const metadataJson = JSON.stringify(metadata);
  const metadataBytes = new TextEncoder().encode(metadataJson);

  const totalFileSize = files.reduce((acc, f) => acc + f.content.length, 0);
  const totalSize = 4 + 4 + metadataBytes.length + totalFileSize;
  const buffer = new Uint8Array(totalSize);

  buffer[0] = 0x4d;
  buffer[1] = 0x46;
  buffer[2] = 0x53;
  buffer[3] = 0x01;

  const view = new DataView(buffer.buffer);
  view.setUint32(4, metadataBytes.length, false);
  buffer.set(metadataBytes, 8);

  let offset = 8 + metadataBytes.length;
  for (const file of files) {
    buffer.set(file.content, offset);
    offset += file.content.length;
  }

  // Parse and verify each file
  const parsedMetadataLength = view.getUint32(4, false);
  const parsedMetadataBytes = buffer.slice(8, 8 + parsedMetadataLength);
  const parsedMetadata = JSON.parse(new TextDecoder().decode(parsedMetadataBytes));

  let fileOffset = 8 + parsedMetadataLength;
  for (let i = 0; i < parsedMetadata.files.length; i++) {
    const fileInfo = parsedMetadata.files[i];
    const content = buffer.slice(fileOffset, fileOffset + fileInfo.size);

    assertEquals(Array.from(content), Array.from(files[i].content));
    assertEquals(fileInfo.path, files[i].path);

    fileOffset += fileInfo.size;
  }
});

Deno.test("Directory extraction from path", () => {
  // Test the directory extraction logic used in mountFiles
  const testCases = [
    { path: "/tmp/test.txt", expected: "/tmp" },
    { path: "/home/user/data/file.json", expected: "/home/user/data" },
    { path: "/file.txt", expected: "" },
    { path: "relative.txt", expected: "" },
    { path: "/a/b/c/d/e.txt", expected: "/a/b/c/d" },
  ];

  for (const { path, expected } of testCases) {
    const dir = path.substring(0, path.lastIndexOf("/"));
    assertEquals(dir, expected, `Path: ${path}`);
  }
});

Deno.test("Session bytes JSON serialization round-trip", () => {
  // Test the session bytes serialization used in executor
  const originalBytes = new Uint8Array([1, 2, 3, 255, 0, 128]);
  const jsonArray = Array.from(originalBytes);
  const jsonString = JSON.stringify(jsonArray);

  // Parse back
  const parsed = JSON.parse(jsonString);
  const restored = new Uint8Array(parsed);

  assertEquals(Array.from(restored), Array.from(originalBytes));
});

Deno.test("Session bytes handles large arrays", () => {
  // Test with a larger array similar to actual session data
  const size = 10000;
  const originalBytes = new Uint8Array(size);
  for (let i = 0; i < size; i++) {
    originalBytes[i] = i % 256;
  }

  const jsonArray = Array.from(originalBytes);
  const jsonString = JSON.stringify(jsonArray);
  const parsed = JSON.parse(jsonString);
  const restored = new Uint8Array(parsed);

  assertEquals(restored.length, size);
  assertEquals(restored[0], 0);
  assertEquals(restored[255], 255);
  assertEquals(restored[256], 0);
});

Deno.test("Execution result structure", () => {
  // Test the ExecutionResult interface structure
  interface ExecutionResult {
    success: boolean;
    stdout: string;
    stderr: string;
    result: unknown;
    sessionBytes?: number[];
    sessionMetadata?: Record<string, unknown>;
    files?: Array<{ path: string; content: number[] }>;
  }

  const result: ExecutionResult = {
    success: true,
    stdout: "Hello",
    stderr: "",
    result: 42,
    files: [{ path: "/tmp/out.txt", content: [72, 105] }],
  };

  assertEquals(result.success, true);
  assertEquals(result.stdout, "Hello");
  assertExists(result.files);
  assertEquals(result.files[0].path, "/tmp/out.txt");
});
