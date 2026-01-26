/**
 * Tests for fs_utils.ts - Pyodide filesystem utilities
 *
 * Run with: deno test --allow-read fs_utils_test.ts
 */

import { assertEquals } from "jsr:@std/assert@1";
import {
  pathExists,
  isDirectory,
  getFileSize,
  readDirEntries,
  joinPath,
  readFileContent,
  isSystemPath,
  snapshotFiles,
  collectFiles,
  collectFilesFromPaths,
} from "./fs_utils.ts";

// Mock Pyodide FS object
function createMockPyodide(filesystem: Record<string, { isDir: boolean; content?: number[]; size?: number }>) {
  return {
    FS: {
      analyzePath: (path: string) => ({
        exists: path in filesystem,
      }),
      stat: (path: string) => {
        if (!(path in filesystem)) {
          throw new Error(`ENOENT: ${path}`);
        }
        const entry = filesystem[path];
        return {
          mode: entry.isDir ? 0o40755 : 0o100644,
          size: entry.size ?? entry.content?.length ?? 0,
        };
      },
      isDir: (mode: number) => (mode & 0o170000) === 0o40000,
      readdir: (path: string) => {
        if (!(path in filesystem) || !filesystem[path].isDir) {
          throw new Error(`ENOTDIR: ${path}`);
        }
        const entries = [".", ".."];
        const prefix = path === "/" ? "/" : path + "/";
        for (const p of Object.keys(filesystem)) {
          if (p !== path && p.startsWith(prefix)) {
            const relative = p.slice(prefix.length);
            const firstPart = relative.split("/")[0];
            if (firstPart && !entries.includes(firstPart)) {
              entries.push(firstPart);
            }
          }
        }
        return entries;
      },
      readFile: (path: string) => {
        if (!(path in filesystem) || filesystem[path].isDir) {
          throw new Error(`ENOENT or EISDIR: ${path}`);
        }
        return new Uint8Array(filesystem[path].content ?? []);
      },
    },
  };
}

// pathExists tests
Deno.test("pathExists returns true for existing path", () => {
  const pyodide = createMockPyodide({
    "/tmp/file.txt": { isDir: false, content: [72, 105] },
  });
  assertEquals(pathExists(pyodide, "/tmp/file.txt"), true);
});

Deno.test("pathExists returns false for non-existing path", () => {
  const pyodide = createMockPyodide({});
  assertEquals(pathExists(pyodide, "/nonexistent"), false);
});

Deno.test("pathExists returns false on FS error", () => {
  const pyodide = {
    FS: {
      analyzePath: () => {
        throw new Error("FS error");
      },
    },
  };
  assertEquals(pathExists(pyodide, "/any"), false);
});

// isDirectory tests
Deno.test("isDirectory returns true for directory", () => {
  const pyodide = createMockPyodide({
    "/tmp": { isDir: true },
  });
  assertEquals(isDirectory(pyodide, "/tmp"), true);
});

Deno.test("isDirectory returns false for file", () => {
  const pyodide = createMockPyodide({
    "/tmp/file.txt": { isDir: false, content: [] },
  });
  assertEquals(isDirectory(pyodide, "/tmp/file.txt"), false);
});

Deno.test("isDirectory returns false for non-existing path", () => {
  const pyodide = createMockPyodide({});
  assertEquals(isDirectory(pyodide, "/nonexistent"), false);
});

// getFileSize tests
Deno.test("getFileSize returns correct size", () => {
  const pyodide = createMockPyodide({
    "/tmp/file.txt": { isDir: false, content: [1, 2, 3, 4, 5] },
  });
  assertEquals(getFileSize(pyodide, "/tmp/file.txt"), 5);
});

Deno.test("getFileSize returns -1 for non-existing file", () => {
  const pyodide = createMockPyodide({});
  assertEquals(getFileSize(pyodide, "/nonexistent"), -1);
});

// readDirEntries tests
Deno.test("readDirEntries returns entries without . and ..", () => {
  const pyodide = createMockPyodide({
    "/tmp": { isDir: true },
    "/tmp/a.txt": { isDir: false, content: [] },
    "/tmp/b.txt": { isDir: false, content: [] },
    "/tmp/subdir": { isDir: true },
  });
  const entries = readDirEntries(pyodide, "/tmp");
  assertEquals(entries.sort(), ["a.txt", "b.txt", "subdir"].sort());
});

Deno.test("readDirEntries returns empty array for non-directory", () => {
  const pyodide = createMockPyodide({
    "/tmp/file.txt": { isDir: false, content: [] },
  });
  assertEquals(readDirEntries(pyodide, "/tmp/file.txt"), []);
});

// joinPath tests
Deno.test("joinPath handles root directory", () => {
  assertEquals(joinPath("/", "file.txt"), "/file.txt");
});

Deno.test("joinPath handles subdirectory", () => {
  assertEquals(joinPath("/tmp", "file.txt"), "/tmp/file.txt");
});

Deno.test("joinPath handles nested path", () => {
  assertEquals(joinPath("/tmp/subdir", "file.txt"), "/tmp/subdir/file.txt");
});

// readFileContent tests
Deno.test("readFileContent returns file content as number array", () => {
  const pyodide = createMockPyodide({
    "/tmp/file.txt": { isDir: false, content: [72, 101, 108, 108, 111] },
  });
  assertEquals(readFileContent(pyodide, "/tmp/file.txt"), [72, 101, 108, 108, 111]);
});

Deno.test("readFileContent returns null for non-existing file", () => {
  const pyodide = createMockPyodide({});
  assertEquals(readFileContent(pyodide, "/nonexistent"), null);
});

Deno.test("readFileContent returns null for directory", () => {
  const pyodide = createMockPyodide({
    "/tmp": { isDir: true },
  });
  assertEquals(readFileContent(pyodide, "/tmp"), null);
});

// isSystemPath tests
Deno.test("isSystemPath returns true for /lib paths", () => {
  assertEquals(isSystemPath("/lib/python3.11"), true);
  assertEquals(isSystemPath("/lib"), true);
});

Deno.test("isSystemPath returns true for /share paths", () => {
  assertEquals(isSystemPath("/share/data"), true);
  assertEquals(isSystemPath("/share"), true);
});

Deno.test("isSystemPath returns false for user paths", () => {
  assertEquals(isSystemPath("/tmp/file.txt"), false);
  assertEquals(isSystemPath("/home/user"), false);
  assertEquals(isSystemPath("/data/output.csv"), false);
});

// snapshotFiles tests
Deno.test("snapshotFiles creates file size snapshot", () => {
  const pyodide = createMockPyodide({
    "/tmp": { isDir: true },
    "/tmp/a.txt": { isDir: false, content: [1, 2, 3] },
    "/tmp/b.txt": { isDir: false, content: [1, 2, 3, 4, 5] },
  });
  const snapshot = snapshotFiles(pyodide, ["/tmp"]);
  assertEquals(snapshot.get("/tmp/a.txt"), 3);
  assertEquals(snapshot.get("/tmp/b.txt"), 5);
});

Deno.test("snapshotFiles handles non-existing paths", () => {
  const pyodide = createMockPyodide({});
  const snapshot = snapshotFiles(pyodide, ["/nonexistent"]);
  assertEquals(snapshot.size, 0);
});

// collectFiles tests
Deno.test("collectFiles collects file content recursively", () => {
  const pyodide = createMockPyodide({
    "/tmp": { isDir: true },
    "/tmp/a.txt": { isDir: false, content: [65] },
    "/tmp/sub": { isDir: true },
    "/tmp/sub/b.txt": { isDir: false, content: [66] },
  });
  const files = collectFiles(pyodide, ["/tmp"]);
  assertEquals(files.length, 2);

  const fileA = files.find(f => f.path === "/tmp/a.txt");
  const fileB = files.find(f => f.path === "/tmp/sub/b.txt");
  assertEquals(fileA?.content, [65]);
  assertEquals(fileB?.content, [66]);
});

// collectFilesFromPaths tests
Deno.test("collectFilesFromPaths filters system paths", () => {
  const pyodide = createMockPyodide({
    "/tmp/user.txt": { isDir: false, content: [1] },
    "/lib/system.so": { isDir: false, content: [2] },
    "/share/data.txt": { isDir: false, content: [3] },
  });
  const files = collectFilesFromPaths(pyodide, ["/tmp/user.txt", "/lib/system.so", "/share/data.txt"]);
  assertEquals(files.length, 1);
  assertEquals(files[0].path, "/tmp/user.txt");
});

Deno.test("collectFilesFromPaths skips directories", () => {
  const pyodide = createMockPyodide({
    "/tmp": { isDir: true },
    "/tmp/file.txt": { isDir: false, content: [1] },
  });
  const files = collectFilesFromPaths(pyodide, ["/tmp", "/tmp/file.txt"]);
  assertEquals(files.length, 1);
  assertEquals(files[0].path, "/tmp/file.txt");
});

Deno.test("collectFilesFromPaths skips non-existing paths", () => {
  const pyodide = createMockPyodide({
    "/tmp/exists.txt": { isDir: false, content: [1] },
  });
  const files = collectFilesFromPaths(pyodide, ["/tmp/exists.txt", "/tmp/missing.txt"]);
  assertEquals(files.length, 1);
  assertEquals(files[0].path, "/tmp/exists.txt");
});
