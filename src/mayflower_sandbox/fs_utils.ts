/**
 * Shared filesystem utilities for Pyodide workers.
 *
 * Extracted from executor.ts and worker_server.ts to reduce duplication
 * and simplify cognitive complexity.
 *
 * Note: These functions intentionally catch and suppress FS errors,
 * returning safe default values. This is by design for graceful degradation.
 */

/**
 * Safely check if FS operation result exists
 */
function safeExists(pyodide: any, path: string): boolean | null {
  const result = pyodide.FS.analyzePath(path);
  return result?.exists ?? null;
}

/**
 * Check if a path exists in Pyodide FS.
 * Returns false for non-existent paths or on any FS error.
 */
export function pathExists(pyodide: any, path: string): boolean {
  try {
    return safeExists(pyodide, path) === true;
  } catch {
    return false; // FS error - treat as non-existent
  }
}

/**
 * Check if a path is a directory in Pyodide FS.
 * Returns false for non-directories or on any FS error.
 */
export function isDirectory(pyodide: any, path: string): boolean {
  try {
    const stat = pyodide.FS.stat(path);
    return pyodide.FS.isDir(stat.mode);
  } catch {
    return false; // Path not accessible - treat as non-directory
  }
}

/**
 * Get file size for a path.
 * Returns -1 on any FS error (file not found, permission denied, etc).
 */
export function getFileSize(pyodide: any, path: string): number {
  try {
    return pyodide.FS.stat(path).size;
  } catch {
    return -1; // File not accessible - return invalid size
  }
}

/**
 * Read directory entries (excluding . and ..).
 * Returns empty array on any FS error.
 */
export function readDirEntries(pyodide: any, path: string): string[] {
  try {
    const entries: string[] = pyodide.FS.readdir(path);
    return entries.filter((e: string) => e !== "." && e !== "..");
  } catch {
    return []; // Directory not readable - return empty
  }
}

/**
 * Build full path from parent and entry
 */
export function joinPath(parent: string, entry: string): string {
  return parent === "/" ? `/${entry}` : `${parent}/${entry}`;
}

/**
 * Read file content from Pyodide FS.
 * Returns null on any FS error (file not found, permission denied, etc).
 */
export function readFileContent(pyodide: any, path: string): number[] | null {
  try {
    const content = pyodide.FS.readFile(path);
    return Array.from(content);
  } catch {
    return null; // File not readable - return null
  }
}

/**
 * Check if path is a system path that should be filtered
 */
export function isSystemPath(path: string): boolean {
  return path.startsWith("/lib") || path.startsWith("/share");
}

/**
 * Process a single path for file snapshotting (recursive)
 */
function snapshotPath(
  pyodide: any,
  path: string,
  snapshot: Map<string, number>,
): void {
  if (!pathExists(pyodide, path)) return;

  if (isDirectory(pyodide, path)) {
    const entries = readDirEntries(pyodide, path);
    for (const entry of entries) {
      snapshotPath(pyodide, joinPath(path, entry), snapshot);
    }
  } else {
    const size = getFileSize(pyodide, path);
    if (size >= 0) {
      snapshot.set(path, size);
    }
  }
}

/**
 * Create a snapshot of file metadata (path + size) for comparison.
 * Used to detect file changes during execution.
 */
export function snapshotFiles(pyodide: any, paths: string[]): Map<string, number> {
  const snapshot = new Map<string, number>();
  for (const path of paths) {
    snapshotPath(pyodide, path, snapshot);
  }
  return snapshot;
}

/**
 * Process a single path for file collection (recursive)
 */
function collectPath(
  pyodide: any,
  path: string,
  files: Array<{ path: string; content: number[] }>,
): void {
  if (!pathExists(pyodide, path)) return;

  if (isDirectory(pyodide, path)) {
    const entries = readDirEntries(pyodide, path);
    for (const entry of entries) {
      collectPath(pyodide, joinPath(path, entry), files);
    }
  } else {
    const content = readFileContent(pyodide, path);
    if (content !== null) {
      files.push({ path, content });
    }
  }
}

/**
 * Collect files from Pyodide filesystem (recursive).
 * Returns file paths with their content.
 */
export function collectFiles(
  pyodide: any,
  paths: string[],
): Array<{ path: string; content: number[] }> {
  const files: Array<{ path: string; content: number[] }> = [];
  for (const path of paths) {
    collectPath(pyodide, path, files);
  }
  return files;
}

/**
 * Collect files from specific paths, filtering system paths.
 * Used with FS.trackingDelegate to collect only user files.
 */
export function collectFilesFromPaths(
  pyodide: any,
  paths: string[],
): Array<{ path: string; content: number[] }> {
  const files: Array<{ path: string; content: number[] }> = [];

  for (const path of paths) {
    if (isSystemPath(path)) continue;
    if (!pathExists(pyodide, path)) continue;
    if (isDirectory(pyodide, path)) continue;

    const content = readFileContent(pyodide, path);
    if (content !== null) {
      files.push({ path, content });
    }
  }

  return files;
}
