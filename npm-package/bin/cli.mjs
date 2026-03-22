#!/usr/bin/env node

/**
 * code-graph-builder MCP server launcher
 *
 * Automatically detects the best way to run the Python MCP server:
 *   1. uvx (fastest, auto-installs in isolated env)
 *   2. pipx (similar to uvx)
 *   3. Direct python3 (requires prior pip install)
 *
 * Usage:
 *   npx code-graph-builder          # auto-detect
 *   npx code-graph-builder --pip    # force pip mode
 */

import { spawn, execFileSync } from "node:child_process";

const PYTHON_PACKAGE = "code-graph-builder";
const MODULE_PATH = "code_graph_builder.mcp.server";

// Pass through all env vars (CGB_WORKSPACE, API keys, etc.)
const env = { ...process.env };

/**
 * Check if a command exists on PATH.
 */
function commandExists(cmd) {
  try {
    execFileSync("which", [cmd], { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

/**
 * Check if the Python package is importable.
 */
function pythonPackageInstalled() {
  try {
    execFileSync("python3", ["-c", `import ${MODULE_PATH.split(".")[0]}`], {
      stdio: "pipe",
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Run a command as the MCP server (replaces this process's stdio).
 */
function runServer(cmd, args) {
  const child = spawn(cmd, args, {
    stdio: "inherit",
    env,
  });

  child.on("error", (err) => {
    console.error(`Failed to start MCP server: ${err.message}`);
    process.exit(1);
  });

  child.on("exit", (code) => {
    process.exit(code ?? 0);
  });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const forceMode = process.argv[2];

if (forceMode === "--pip" || forceMode === "--python") {
  // Force direct python3 mode
  if (!pythonPackageInstalled()) {
    console.error(
      `Error: Python package '${PYTHON_PACKAGE}' is not installed.\n` +
        `Run: pip install ${PYTHON_PACKAGE}`
    );
    process.exit(1);
  }
  runServer("python3", ["-m", MODULE_PATH]);
} else if (commandExists("uvx")) {
  // Preferred: uvx auto-installs in isolated env
  runServer("uvx", [PYTHON_PACKAGE, ...process.argv.slice(2)]);
} else if (commandExists("uv")) {
  // uv available but not uvx — use uv tool run
  runServer("uv", ["tool", "run", PYTHON_PACKAGE, ...process.argv.slice(2)]);
} else if (commandExists("pipx")) {
  // pipx: similar to uvx
  runServer("pipx", ["run", PYTHON_PACKAGE, ...process.argv.slice(2)]);
} else if (pythonPackageInstalled()) {
  // Fallback: direct python3
  runServer("python3", ["-m", MODULE_PATH]);
} else {
  // Nothing works — guide the user
  console.error(
    `code-graph-builder MCP server requires Python 3.10+.\n\n` +
      `Install options (pick one):\n` +
      `  1. pip install ${PYTHON_PACKAGE}    # then: npx code-graph-builder --pip\n` +
      `  2. Install uv (recommended):         curl -LsSf https://astral.sh/uv/install.sh | sh\n` +
      `     Then: npx code-graph-builder      # auto-installs via uvx\n` +
      `  3. Install pipx:                     pip install pipx\n` +
      `     Then: npx code-graph-builder      # auto-installs via pipx\n`
  );
  process.exit(1);
}
