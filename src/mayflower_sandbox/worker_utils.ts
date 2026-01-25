/**
 * Pure utility functions for the worker server
 * Extracted to allow testing without pyodide dependency
 */

/**
 * Convert unknown error to string safely
 */
export function errorToString(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  if (typeof e === "number") return `${e}`;
  if (typeof e === "boolean") return e ? "true" : "false";
  if (typeof e === "bigint") return `${e}`;
  if (e === null) return "null";
  if (e === undefined) return "undefined";
  // For objects and anything else, use JSON.stringify
  try {
    return JSON.stringify(e);
  } catch {
    return "[object]";
  }
}

/**
 * Filter out micropip package loading messages from stdout
 * Same logic as legacy executor.ts
 */
export function filterMicropipMessages(stdout: string): string {
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
 * Create stdout write handler
 */
export function createStdoutHandler(
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
export function createSuppressedStdout(): { write: (buf: Uint8Array) => number } {
  return { write: (buf: Uint8Array) => buf.length };
}

/**
 * Create file tracking delegate
 */
export function createFileTracker(): {
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
export function findChangedFiles(
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
