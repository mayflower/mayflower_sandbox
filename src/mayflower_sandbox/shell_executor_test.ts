import {
  assertEquals,
  assertExists,
  assertStringIncludes,
} from "jsr:@std/assert@1";
import { dirname, fromFileUrl, join } from "jsr:@std/path@1";

import { executeShell, parseShellExpression } from "./shell_executor.ts";

const encoder = new TextEncoder();
const shellDir = dirname(fromFileUrl(import.meta.url));
const busyboxDir = join(shellDir, "busybox");

async function ensureBusybox(): Promise<void> {
  try {
    await Deno.stat(join(busyboxDir, "busybox.js"));
    await Deno.stat(join(busyboxDir, "busybox.wasm"));
  } catch {
    throw new Deno.errors.NotFound("busybox assets not available");
  }
}

Deno.test("parseShellExpression keeps quoted operators literal", () => {
  const parsed = parseShellExpression(`echo 'a && b | c ; d'`);

  assertEquals(parsed.items.length, 1);
  const first = parsed.items[0].first;
  assertEquals(first.type, "command");
  if (first.type !== "command") {
    throw new Error("expected simple command");
  }
  assertEquals(first.command.argv, ["echo", "a && b | c ; d"]);
});

Deno.test("executeShell handles OR fallback", async () => {
  try {
    await ensureBusybox();
  } catch {
    return;
  }

  const result = await executeShell({
    command: `cat config.yaml || echo "config not found, using defaults"`,
    busyboxDir,
  });

  assertEquals(result.success, true);
  assertEquals(result.exit_code, 0);
  assertStringIncludes(result.stdout, "config not found, using defaults");
});

Deno.test("executeShell respects shell precedence for AND-OR chains", async () => {
  try {
    await ensureBusybox();
  } catch {
    return;
  }

  const result = await executeShell({
    command: "false && echo a || echo b && echo c",
    busyboxDir,
  });

  assertEquals(result.success, true);
  assertEquals(result.exit_code, 0);
  assertEquals(result.stdout, "b\nc");
});

Deno.test("executeShell treats && and || as left associative", async () => {
  try {
    await ensureBusybox();
  } catch {
    return;
  }

  const result = await executeShell({
    command: "true || echo a && echo b",
    busyboxDir,
  });

  assertEquals(result.success, true);
  assertEquals(result.exit_code, 0);
  assertEquals(result.stdout, "b");
});

Deno.test("executeShell uses last pipeline stage exit status", async () => {
  try {
    await ensureBusybox();
  } catch {
    return;
  }

  const result = await executeShell({
    command: "false | true && echo ok",
    busyboxDir,
  });

  assertEquals(result.success, true);
  assertEquals(result.exit_code, 0);
  assertStringIncludes(result.stdout, "ok");
});

Deno.test("executeShell supports file-backed pipelines", async () => {
  try {
    await ensureBusybox();
  } catch {
    return;
  }

  const result = await executeShell({
    command: `cat access.log | grep "500" | sort | head -n 2`,
    busyboxDir,
    files: {
      "/access.log": encoder.encode("200 ok\n500 err-b\n404 nope\n500 err-a\n"),
    },
  });

  assertEquals(result.success, true);
  assertEquals(result.exit_code, 0);
  assertEquals(result.stdout, "500 err-a\n500 err-b");
});

Deno.test("executeShell persists files created inside a pipeline", async () => {
  try {
    await ensureBusybox();
  } catch {
    return;
  }

  const result = await executeShell({
    command: "echo hi | tee /tmp/p.txt >/dev/null ; cat /tmp/p.txt",
    busyboxDir,
  });

  assertEquals(result.success, true);
  assertStringIncludes(result.stdout, "hi");
  assertExists(result.created_files);
  assertEquals(
    result.created_files?.some((file) => file.path === "/tmp/p.txt"),
    true,
  );
});
