#!/usr/bin/env node

/**
 * terrain MCP server launcher & setup wizard
 *
 * Usage:
 *   terrain              # interactive setup (first run)
 *   terrain server       # start MCP server (used by MCP clients)
 *   terrain setup        # re-run setup wizard
 *   terrain update       # check & install updates
 *   terrain move         # move workspace to a new location
 *   terrain pip          # force python3 direct mode
 */

import { spawn, execFileSync, execSync } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync, readdirSync, copyFileSync, renameSync, cpSync, unlinkSync, statSync } from "node:fs";
import { homedir, platform, tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PYTHON_PACKAGE = "terrain";
const MODULE_PATH = "terrain.entrypoints.mcp.server";
const WORKSPACE_DIR = join(homedir(), ".terrain");
const ENV_FILE = join(WORKSPACE_DIR, ".env");
const UPDATE_CHECK_FILE = join(WORKSPACE_DIR, ".last-update-check");
const UPDATE_LOG_FILE = join(WORKSPACE_DIR, ".update-log");
const UPDATE_PENDING_FILE = join(WORKSPACE_DIR, ".update-pending");
const IS_WIN = platform() === "win32";
// Removed hardcoded PyPI index — pip will use the user's configured source
// (e.g. mirrors in pip.conf / pip.ini)

// How often to check for updates (4 hours)
const UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

// ---------------------------------------------------------------------------
// Tree-style UI helpers
// ---------------------------------------------------------------------------

const T = {
  // Box drawing
  TOP:    "╭",
  BOT:    "╰",
  SIDE:   "│",
  TEE:    "├",
  BEND:   "╰",
  DASH:   "─",
  // Status
  OK:     "✓",
  FAIL:   "✗",
  WARN:   "⚠",
  WORK:   "…",
  DOT:    "●",
  // Indents
  PIPE:   "│  ",
  SPACE:  "   ",
  BRANCH: "├─ ",
  LAST:   "╰─ ",
};

/**
 * Interactive single-select menu.
 * Arrow keys to navigate, Space to select, Enter to confirm.
 * Returns the index of the selected option, -1 if cancelled (Ctrl+C),
 * or -2 if the user pressed ← (back to previous step).
 *
 * @param {string[]} options - Display labels for each option
 * @param {string} prefix - Tree prefix for each line (e.g. "  │  ")
 * @param {number} defaultIndex - Initially highlighted index
 * @param {boolean} allowBack - Whether ← arrow triggers back (-2)
 * @returns {Promise<number>}
 */
function selectMenu(options, prefix = "  ", defaultIndex = 0, allowBack = false) {
  return new Promise((resolve) => {
    const out = process.stderr;
    let cursor = defaultIndex;
    let selected = -1;

    const RADIO_ON  = "◉";
    const RADIO_OFF = "○";
    const DIM   = "\x1b[2m";
    const BOLD  = "\x1b[1m";
    const CYAN  = "\x1b[36m";
    const RESET = "\x1b[0m";

    const backHint = allowBack ? `${DIM}  ← back${RESET}` : "";

    function render(initial = false) {
      // Move cursor up to overwrite previous render (skip on first draw)
      if (!initial) {
        out.write(`\x1b[${options.length + (allowBack ? 1 : 0)}A`);
      }
      for (let i = 0; i < options.length; i++) {
        const isActive = i === cursor;
        const isSelected = i === selected;
        const radio = (isSelected || (selected === -1 && isActive)) && isActive
          ? `${CYAN}${RADIO_ON}${RESET}`
          : `${DIM}${RADIO_OFF}${RESET}`;
        const label = isActive
          ? `${BOLD}${CYAN}${options[i]}${RESET}`
          : `${options[i]}`;
        // Clear line then write
        out.write(`\x1b[2K${prefix}${radio} ${label}\n`);
      }
      if (allowBack) {
        out.write(`\x1b[2K${prefix}${DIM}← Back to previous step${RESET}\n`);
      }
    }

    // Hide cursor
    out.write("\x1b[?25l");
    render(true);

    const stdin = process.stdin;
    const wasRaw = stdin.isRaw;
    stdin.setRawMode(true);
    stdin.resume();

    function cleanup() {
      stdin.setRawMode(wasRaw || false);
      stdin.removeListener("data", onKey);
      // Show cursor
      out.write("\x1b[?25h");
    }

    function onKey(buf) {
      const key = buf.toString();

      // Ctrl+C
      if (key === "\x03") {
        cleanup();
        resolve(-1);
        return;
      }

      // Arrow left — back to previous step
      if (key === "\x1b[D" && allowBack) {
        cleanup();
        resolve(-2);
        return;
      }

      // Arrow up / k
      if (key === "\x1b[A" || key === "k") {
        cursor = (cursor - 1 + options.length) % options.length;
        render();
        return;
      }

      // Arrow down / j
      if (key === "\x1b[B" || key === "j") {
        cursor = (cursor + 1) % options.length;
        render();
        return;
      }

      // Space — toggle selection
      if (key === " ") {
        selected = cursor;
        render();
        return;
      }

      // Enter — confirm
      if (key === "\r" || key === "\n") {
        if (selected === -1) selected = cursor;
        cleanup();
        resolve(selected);
        return;
      }
    }

    stdin.on("data", onKey);
  });
}

/**
 * Interactive multi-select checkbox menu.
 * Arrow keys to navigate, Space to toggle, Enter to confirm.
 * Returns array of selected indices, null on Ctrl+C, -2 on ← (back).
 *
 * @param {string[]} options - Display labels
 * @param {number[]} preSelected - Initially checked indices
 * @param {string} prefix - Tree prefix for each line
 * @param {boolean} allowBack - Whether ← arrow triggers back (-2)
 * @returns {Promise<number[]|null|-2>}
 */
/**
 * @param {number[]} lockedIndices - Always-checked indices that cannot be toggled.
 *   Rendered as dim green [x] with dim label to indicate they are core/required.
 */
function multiSelectMenu(options, preSelected = [], prefix = "  ", allowBack = false, lockedIndices = []) {
  return new Promise((resolve) => {
    const out = process.stderr;
    let cursor = 0;
    const locked = new Set(lockedIndices);
    const selected = new Set(preSelected);
    // Locked items are always selected
    for (const i of locked) selected.add(i);

    const DIM   = "\x1b[2m";
    const BOLD  = "\x1b[1m";
    const CYAN  = "\x1b[36m";
    const GREEN = "\x1b[32m";
    const RESET = "\x1b[0m";

    const lineCount = options.length + 1 + (allowBack ? 1 : 0); // +1 hint

    function render(initial = false) {
      if (!initial) out.write(`\x1b[${lineCount}A`);
      out.write(`\x1b[2K${prefix}${DIM}Space to toggle · Enter to confirm${allowBack ? " · ← back" : ""}${RESET}\n`);
      for (let i = 0; i < options.length; i++) {
        const isActive  = i === cursor;
        const isLocked  = locked.has(i);
        const isChecked = selected.has(i);

        let box, label;
        if (isLocked) {
          // Dim green checkbox + dim label — visually "selected but not toggleable"
          box   = `${DIM}${GREEN}[x]${RESET}`;
          label = isActive
            ? `${DIM}${GREEN}${options[i]}${RESET}`
            : `${DIM}${options[i]}${RESET}`;
        } else {
          box = isChecked
            ? `${GREEN}[x]${RESET}`
            : `${DIM}[ ]${RESET}`;
          label = isActive
            ? `${BOLD}${CYAN}${options[i]}${RESET}`
            : options[i];
        }
        out.write(`\x1b[2K${prefix}${box} ${label}\n`);
      }
      if (allowBack) {
        out.write(`\x1b[2K${prefix}${DIM}← Back to previous step${RESET}\n`);
      }
    }

    out.write("\x1b[?25l");
    render(true);

    const stdin = process.stdin;
    const wasRaw = stdin.isRaw;
    stdin.setRawMode(true);
    stdin.resume();

    function cleanup() {
      stdin.setRawMode(wasRaw || false);
      stdin.removeListener("data", onKey);
      out.write("\x1b[?25h");
    }

    function onKey(buf) {
      const key = buf.toString();
      if (key === "\x03") { cleanup(); resolve(null); return; }
      if (key === "\x1b[D" && allowBack) { cleanup(); resolve(-2); return; }
      if (key === "\x1b[A" || key === "k") { cursor = (cursor - 1 + options.length) % options.length; render(); return; }
      if (key === "\x1b[B" || key === "j") { cursor = (cursor + 1) % options.length; render(); return; }
      if (key === " ") {
        // Locked items cannot be toggled
        if (locked.has(cursor)) { render(); return; }
        if (selected.has(cursor)) selected.delete(cursor);
        else selected.add(cursor);
        render();
        return;
      }
      if (key === "\r" || key === "\n") { cleanup(); resolve([...selected]); return; }
    }
    stdin.on("data", onKey);
  });
}

function box(title) {
  const pad = 54;
  const inner = `  ${title}  `;
  const fill = pad - inner.length;
  const left = Math.floor(fill / 2);
  const right = fill - left;
  return [
    `  ${T.TOP}${"─".repeat(pad)}╮`,
    `  ${T.SIDE}${" ".repeat(left)}${inner}${" ".repeat(right)}${T.SIDE}`,
    `  ${T.BOT}${"─".repeat(pad)}╯`,
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function commandExists(cmd) {
  try {
    const checker = IS_WIN ? "where" : "which";
    execFileSync(checker, [cmd], { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

function findPython() {
  const candidates = IS_WIN
    ? ["python", "python3", "py"]
    : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      const ver = execFileSync(cmd, ["--version"], { stdio: "pipe" }).toString().trim();
      if (ver.includes("3.")) return { cmd, ver };
    } catch { /* skip */ }
  }
  return null;
}

const pythonInfo = findPython();
const PYTHON_CMD = pythonInfo?.cmd || null;
const PYTHON_VER = pythonInfo?.ver || null;

function pythonPackageInstalled() {
  if (!PYTHON_CMD) return false;
  try {
    execFileSync(PYTHON_CMD, ["-c", `import ${MODULE_PATH.split(".")[0]}`], {
      stdio: "pipe",
    });
    return true;
  } catch {
    return false;
  }
}

function getPackageVersion() {
  if (!PYTHON_CMD) return null;
  try {
    return execFileSync(PYTHON_CMD, ["-c",
      `import terrain; print(getattr(terrain, '__version__', 'unknown'))`
    ], { stdio: "pipe" }).toString().trim();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Version helpers for auto-update
// ---------------------------------------------------------------------------

/** Read the local npm package version from package.json */
function getLocalNpmVersion() {
  try {
    const pkgPath = join(dirname(fileURLToPath(import.meta.url)), "..", "package.json");
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    return pkg.version || null;
  } catch {
    return null;
  }
}

/**
 * Fetch the latest version from the npm registry (terrain-ai).
 * Returns null on network error or timeout.
 */
function getLatestNpmVersion() {
  try {
    const out = execSync("npm view terrain-ai version", {
      encoding: "utf-8", stdio: "pipe", timeout: 10_000,
    });
    return out.trim() || null;
  } catch {
    return null;
  }
}

/**
 * Fetch the latest version from PyPI for the terrain package.
 * Uses the PyPI JSON API via a temp .py file (avoids cmd.exe quoting issues
 * on Windows where nested single/double quotes break).
 */
function getLatestPypiVersion() {
  if (!PYTHON_CMD) return null;
  const tmpPy = join(tmpdir(), `terrain-pypi-${process.pid}.py`);
  try {
    writeFileSync(tmpPy, [
      "import urllib.request, json",
      "d = json.loads(urllib.request.urlopen('https://pypi.org/pypi/terrain-ai/json', timeout=5).read())",
      "print(d['info']['version'])",
    ].join("\n"), "utf-8");
    const out = execFileSync(PYTHON_CMD, [tmpPy], {
      encoding: "utf-8", stdio: "pipe", timeout: 10_000,
    });
    return out.trim() || null;
  } catch {
    return null;
  } finally {
    try { unlinkSync(tmpPy); } catch {}
  }
}

/**
 * Simple semver compare: returns 1 if a > b, -1 if a < b, 0 if equal.
 * Only handles numeric x.y.z; treats missing segments as 0.
 */
function semverCompare(a, b) {
  const pa = (a || "0").split(".").map(Number);
  const pb = (b || "0").split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] || 0, vb = pb[i] || 0;
    if (va > vb) return 1;
    if (va < vb) return -1;
  }
  return 0;
}

/** Check if auto-updater is disabled via env or .env config */
function isAutoUpdateDisabled() {
  if (process.env.DISABLE_AUTOUPDATER === "1") return true;
  try {
    const envVars = loadEnvFile();
    if (envVars.DISABLE_AUTOUPDATER === "1") return true;
  } catch { /* non-critical */ }
  return false;
}

/** Check if enough time has passed since the last update check */
function shouldCheckForUpdate() {
  if (isAutoUpdateDisabled()) return false;
  try {
    if (!existsSync(UPDATE_CHECK_FILE)) return true;
    const ts = parseInt(readFileSync(UPDATE_CHECK_FILE, "utf-8").trim(), 10);
    return Date.now() - ts > UPDATE_CHECK_INTERVAL_MS;
  } catch {
    return true;
  }
}

/** Record the current timestamp as last update check */
function recordUpdateCheck() {
  try {
    mkdirSync(WORKSPACE_DIR, { recursive: true });
    writeFileSync(UPDATE_CHECK_FILE, String(Date.now()), "utf-8");
  } catch { /* non-critical */ }
}

/** Write a pending-update marker so next startup knows an update was applied */
function writePendingMarker(info) {
  try {
    writeFileSync(UPDATE_PENDING_FILE, JSON.stringify(info), "utf-8");
  } catch { /* non-critical */ }
}

/** Read & clear the pending-update marker. Returns info object or null. */
function readAndClearPendingMarker() {
  try {
    if (!existsSync(UPDATE_PENDING_FILE)) return null;
    const info = JSON.parse(readFileSync(UPDATE_PENDING_FILE, "utf-8"));
    rmSync(UPDATE_PENDING_FILE, { force: true });
    return info;
  } catch {
    try { rmSync(UPDATE_PENDING_FILE, { force: true }); } catch {}
    return null;
  }
}

/**
 * Show a one-line notice if an update was applied in a previous background run.
 * Called on every startup — very cheap (just reads a marker file).
 */
function showPendingUpdateNotice() {
  const info = readAndClearPendingMarker();
  if (!info) return;
  const log = (msg) => process.stderr.write(msg + "\n");
  log("");
  log(`  ${T.OK} terrain updated in background — restart to use new version`);
  if (info.npm) log(`    CLI:    ${info.npm.from} → ${info.npm.to}`);
  if (info.py)  log(`    Python: ${info.py.from} → ${info.py.to}`);
  log("");
}

/**
 * Background auto-update — Claude Code style.
 *
 * 1. Checks once per UPDATE_CHECK_INTERVAL_MS (4h)
 * 2. Writes a temp .js file, spawns it as a detached child process
 * 3. Never blocks or interrupts the current server session
 * 4. Writes a marker file so the NEXT startup shows "updated!" notice
 * 5. Respects DISABLE_AUTOUPDATER=1
 *
 * Windows compatibility:
 *   - Uses a temp file instead of `node -e` to avoid 8191-char cmd limit
 *   - `windowsHide: true` prevents console window flash
 *   - Python commands use helper .py temp files to avoid cmd.exe quote hell
 *   - npm global install may require elevated permissions on Windows
 */
function backgroundAutoUpdate() {
  if (!shouldCheckForUpdate()) return;
  recordUpdateCheck();

  // Write the updater as a temp .js file (avoids Windows command-line length
  // limit of 8191 chars and cmd.exe quoting issues with `node -e`).
  const updaterFile = join(tmpdir(), `terrain-update-${process.pid}.cjs`);

  const updaterScript = `
"use strict";
const { execSync, execFileSync } = require("child_process");
const fs = require("fs");

const LOG     = ${JSON.stringify(UPDATE_LOG_FILE)};
const PENDING = ${JSON.stringify(UPDATE_PENDING_FILE)};
const PKG_JSON = ${JSON.stringify(join(dirname(fileURLToPath(import.meta.url)), "..", "package.json"))};
const PYTHON  = ${JSON.stringify(PYTHON_CMD || "")};
const IS_WIN  = process.platform === "win32";
const SELF    = ${JSON.stringify(updaterFile)};

function log(msg) {
  try { fs.appendFileSync(LOG, new Date().toISOString() + " " + msg + "\\n"); } catch {}
}

function semverCmp(a, b) {
  const pa = (a || "0").split(".").map(Number);
  const pb = (b || "0").split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] || 0, vb = pb[i] || 0;
    if (va > vb) return 1;
    if (va < vb) return -1;
  }
  return 0;
}

/** Run a Python snippet and return stdout. Uses a temp .py file to avoid
 *  cmd.exe quoting issues on Windows (no nested single/double quote pain). */
function pyEval(code) {
  const tmp = require("os").tmpdir() + "/terrain-py-" + process.pid + ".py";
  try {
    fs.writeFileSync(tmp, code, "utf-8");
    return execFileSync(PYTHON, [tmp], {
      encoding: "utf-8", stdio: "pipe", timeout: 15000
    }).trim();
  } finally {
    try { fs.unlinkSync(tmp); } catch {}
  }
}

function cleanup() {
  try { fs.unlinkSync(SELF); } catch {}
}

try {
  log("Update check started");
  const info = {};

  // --- Check npm ---
  let localNpm = null, latestNpm = null;
  try {
    localNpm = JSON.parse(fs.readFileSync(PKG_JSON, "utf-8")).version || null;
  } catch {}
  try {
    latestNpm = execSync("npm view terrain-ai version", {
      encoding: "utf-8", stdio: "pipe", timeout: 15000, shell: true
    }).trim();
  } catch {}

  if (localNpm && latestNpm && semverCmp(latestNpm, localNpm) > 0) {
    log("CLI outdated: " + localNpm + " -> " + latestNpm);
    try {
      execSync("npm install -g terrain-ai@latest", {
        stdio: "pipe", shell: true, timeout: 120000
      });
      info.npm = { from: localNpm, to: latestNpm };
      log("CLI updated to " + latestNpm);
    } catch (e) {
      log("CLI update failed: " + e.message);
    }
  }

  // --- Check Python ---
  if (PYTHON) {
    let localPy = null, latestPy = null;
    try {
      localPy = pyEval("import terrain; print(getattr(terrain, '__version__', ''))") || null;
    } catch {}
    try {
      latestPy = pyEval(
        "import urllib.request, json\\n" +
        "d = json.loads(urllib.request.urlopen('https://pypi.org/pypi/terrain-ai/json', timeout=5).read())\\n" +
        "print(d['info']['version'])"
      ) || null;
    } catch {}

    if (localPy && latestPy && semverCmp(latestPy, localPy) > 0) {
      log("Python outdated: " + localPy + " -> " + latestPy);

      // Find pip — try common commands, then python -m pip
      let pip = null;
      const pips = IS_WIN ? ["pip", "pip3"] : ["pip3", "pip"];
      for (const p of pips) {
        try {
          execSync(p + " --version", { stdio: "pipe", timeout: 5000, shell: true });
          pip = [p]; break;
        } catch {}
      }
      if (!pip) {
        try {
          execFileSync(PYTHON, ["-m", "pip", "--version"], { stdio: "pipe", timeout: 5000 });
          pip = [PYTHON, "-m", "pip"];
        } catch {}
      }

      if (pip) {
        try {
          const args = [...pip.slice(1), "install", "--upgrade", "--prefer-binary", "terrain-ai[treesitter-full]"];
          execFileSync(pip[0], args, { stdio: "pipe", timeout: 300000 });
          info.py = { from: localPy, to: latestPy };
          log("Python updated to " + latestPy);
        } catch (e) {
          log("Python update failed: " + e.message);
        }
      } else {
        log("pip not found, skipping Python update");
      }
    }
  }

  // --- Write pending marker if anything was updated ---
  if (info.npm || info.py) {
    fs.writeFileSync(PENDING, JSON.stringify(info), "utf-8");
    log("Pending marker written");
  } else {
    log("Everything up to date");
  }
} catch (e) {
  log("Update check error: " + (e.message || e));
} finally {
  cleanup();
}
`;

  // Write temp script & spawn detached — fire and forget
  try {
    writeFileSync(updaterFile, updaterScript, "utf-8");
    const child = spawn(
      process.execPath,
      [updaterFile],
      {
        detached: true,
        stdio: "ignore",
        windowsHide: true,  // prevent console window flash on Windows
        env: { ...process.env, DISABLE_AUTOUPDATER: "1" }, // prevent recursion
      }
    );
    child.unref();
  } catch {
    // Cleanup temp file if spawn failed
    try { unlinkSync(updaterFile); } catch {}
  }
}

function loadEnvFile() {
  if (!existsSync(ENV_FILE)) return {};
  const vars = {};
  for (const line of readFileSync(ENV_FILE, "utf-8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    vars[key] = val;
  }
  return vars;
}

function saveEnvFile(vars) {
  mkdirSync(WORKSPACE_DIR, { recursive: true });
  const lines = [
    "# terrain configuration",
    "# Generated by setup wizard. Edit freely.",
    "",
  ];
  for (const [key, val] of Object.entries(vars)) {
    if (val) lines.push(`${key}=${val}`);
  }
  lines.push("");
  writeFileSync(ENV_FILE, lines.join("\n"), "utf-8");
}

function mask(s) {
  if (!s || s.length < 8) return s ? "****" : "(not set)";
  return s.slice(0, 4) + "****" + s.slice(-4);
}

function findPip() {
  for (const cmd of IS_WIN ? ["pip", "pip3"] : ["pip3", "pip"]) {
    if (commandExists(cmd)) return [cmd];
  }
  if (PYTHON_CMD) {
    try {
      execFileSync(PYTHON_CMD, ["-m", "pip", "--version"], { stdio: "pipe" });
      return [PYTHON_CMD, "-m", "pip"];
    } catch { /* skip */ }
  }
  return null;
}

/**
 * Clear npx cache for terrain to ensure latest version.
 */
function clearNpxCache() {
  try {
    const cacheDir = execSync("npm config get cache", { stdio: "pipe", shell: true })
      .toString().trim();
    const npxCacheDir = join(cacheDir, "_npx");

    if (existsSync(npxCacheDir)) {
      for (const entry of readdirSync(npxCacheDir)) {
        const pkgJsonPath = join(npxCacheDir, entry, "node_modules", "terrain", "package.json");
        const altPkgJson = join(npxCacheDir, entry, "package.json");
        try {
          let found = false;
          if (existsSync(pkgJsonPath)) {
            found = true;
          } else if (existsSync(altPkgJson)) {
            const content = readFileSync(altPkgJson, "utf-8");
            if (content.includes("terrain")) found = true;
          }
          if (found) {
            rmSync(join(npxCacheDir, entry), { recursive: true, force: true });
          }
        } catch { /* skip */ }
      }
    }
  } catch { /* cache clear is best-effort */ }
}

// ---------------------------------------------------------------------------
// Interactive setup wizard
// ---------------------------------------------------------------------------

async function runSetup() {
  let rl = createInterface({
    input: process.stdin,
    output: process.stderr,
  });

  let ask = (q) => new Promise((resolve) => rl.question(q, resolve));
  const log = (msg = "") => process.stderr.write(msg + "\n");

  log();
  log(box("terrain  Setup Wizard"));
  log();

  // --- Step -1: Uninstall legacy code-graph-builder MCP tool ---
  if (commandExists("claude")) {
    log(`  ${T.DOT} Removing legacy MCP tool`);
    log(`  ${T.SIDE}`);
    try {
      execSync("claude mcp remove code-graph-builder", { stdio: "pipe", shell: true });
      log(`  ${T.LAST} ${T.OK} Removed code-graph-builder MCP tool`);
    } catch {
      log(`  ${T.LAST} ${T.OK} No legacy MCP tool found (skipped)`);
    }
    log();
  }

  // --- Step 0: Clear npx cache ---
  log(`  ${T.DOT} Preparing`);
  log(`  ${T.SIDE}`);
  log(`  ${T.BRANCH} Clearing npx cache...`);

  await clearNpxCache();

  log(`  ${T.LAST} ${T.OK} Cache cleared`);
  log();

  // --- Step 0.5: Migrate from code-graph-builder (legacy) ---
  const OLD_WORKSPACE = join(homedir(), ".code-graph-builder");
  if (existsSync(OLD_WORKSPACE)) {
    log(`  ${T.DOT} Migrating from code-graph-builder`);
    log(`  ${T.SIDE}`);

    // Rename ~/.code-graph-builder -> ~/.terrain
    if (!existsSync(WORKSPACE_DIR)) {
      let migrated = false;
      try {
        renameSync(OLD_WORKSPACE, WORKSPACE_DIR);
        migrated = true;
        log(`  ${T.BRANCH} ${T.OK} Renamed ${OLD_WORKSPACE} -> ${WORKSPACE_DIR}`);
      } catch {
        // renameSync can fail on Windows (cross-device, locked files, etc.)
        // Fall back to recursive copy, then best-effort delete
        try {
          cpSync(OLD_WORKSPACE, WORKSPACE_DIR, { recursive: true });
          migrated = true;
          log(`  ${T.BRANCH} ${T.OK} Copied ${OLD_WORKSPACE} -> ${WORKSPACE_DIR}`);
        } catch (cpErr) {
          log(`  ${T.BRANCH} ${T.WARN} Migration failed: ${cpErr.message}`);
        }
        // Best-effort removal of the old directory.
        // On Windows files may be locked by another process — try
        // rmSync first, then fall back to "rd /s /q" which can
        // sometimes succeed where Node's fs fails.
        if (migrated) {
          try {
            rmSync(OLD_WORKSPACE, { recursive: true, force: true });
          } catch {
            if (IS_WIN) {
              try {
                execSync(`rd /s /q "${OLD_WORKSPACE}"`, { stdio: "pipe", shell: true });
              } catch { /* still locked */ }
            }
            if (existsSync(OLD_WORKSPACE)) {
              log(`  ${T.BRANCH} ${T.WARN} Could not remove ${OLD_WORKSPACE} (may be locked by another program). Please delete it manually.`);
            }
          }
        }
      }
    } else {
      log(`  ${T.BRANCH} ${T.WARN} ${WORKSPACE_DIR} already exists, skipping rename`);
    }

    // Uninstall old code-graph-builder pip package
    const pip = findPip();
    if (pip) {
      try {
        const checkCmd = [...pip, "show", "code-graph-builder"].map(s => `"${s}"`).join(" ");
        execSync(checkCmd, { stdio: "pipe", shell: true });
        // If we reach here, the package is installed — uninstall it
        try {
          execSync(
            [...pip, "uninstall", "-y", "code-graph-builder"].map(s => `"${s}"`).join(" "),
            { stdio: "pipe", shell: true }
          );
          log(`  ${T.BRANCH} ${T.OK} Uninstalled code-graph-builder`);
        } catch {
          log(`  ${T.BRANCH} ${T.WARN} Failed to uninstall code-graph-builder (try: pip uninstall code-graph-builder)`);
        }
      } catch {
        // pip show failed → package not installed, nothing to do
      }
    }

    log(`  ${T.LAST} ${T.OK} Migration complete`);
    log();
  }

  // Load existing config
  const existing = loadEnvFile();

  // Step results — preserved across back/forward navigation
  let workspace = existing.CGB_WORKSPACE || WORKSPACE_DIR;
  let llmKey = existing.LLM_API_KEY || "";
  let llmBaseUrl = existing.LLM_BASE_URL || "";
  let llmModel = existing.LLM_MODEL || "";
  let llmProviderName = "skipped";
  // Detect which embedding env var names were previously used
  let embedKeyEnv = existing.EMBED_API_KEY ? "EMBED_API_KEY"
    : existing.DASHSCOPE_API_KEY ? "DASHSCOPE_API_KEY"
    : "DASHSCOPE_API_KEY";
  let embedUrlEnv = existing.EMBED_BASE_URL ? "EMBED_BASE_URL"
    : existing.DASHSCOPE_BASE_URL ? "DASHSCOPE_BASE_URL"
    : "DASHSCOPE_BASE_URL";
  let embedKey = existing[embedKeyEnv] || "";
  let embedUrl = existing[embedUrlEnv] || "";
  let embedModel = existing.EMBED_MODEL || "";
  let embedProviderName = "skipped";

  const llmOptions = [
    "Moonshot / Kimi      platform.moonshot.cn",
    "OpenAI               platform.openai.com",
    "DeepSeek             platform.deepseek.com",
    "OpenRouter           openrouter.ai",
    "LiteLLM Proxy        localhost:4000",
    "Custom endpoint",
    "Skip (configure later)",
  ];

  const llmProviders = [
    { name: "Moonshot",   url: "https://api.moonshot.cn/v1",   model: "kimi-k2.5" },
    { name: "OpenAI",     url: "https://api.openai.com/v1",    model: "gpt-4o" },
    { name: "DeepSeek",   url: "https://api.deepseek.com/v1",  model: "deepseek-chat" },
    { name: "OpenRouter",  url: "https://openrouter.ai/api/v1", model: "anthropic/claude-sonnet-4" },
    { name: "LiteLLM",    url: "http://localhost:4000/v1",     model: "gpt-4o" },
  ];

  const embedOptions = [
    "DashScope / Qwen     dashscope.console.aliyun.com  (free tier)",
    "OpenAI Embeddings    platform.openai.com",
    "Custom endpoint",
    "Skip (configure later)",
  ];

  const embedProvidersList = [
    { name: "DashScope", url: "https://dashscope.aliyuncs.com/api/v1", model: "text-embedding-v4", keyEnv: "DASHSCOPE_API_KEY", urlEnv: "DASHSCOPE_BASE_URL" },
    { name: "OpenAI",    url: "https://api.openai.com/v1",             model: "text-embedding-3-small", keyEnv: "OPENAI_API_KEY", urlEnv: "OPENAI_BASE_URL" },
  ];

  // --- Language items (core = always installed, locked in UI; optional = user choice) ---
  const ALL_LANG_ITEMS = [
    { label: "Python",     pkg: null,                  locked: true  },
    { label: "JavaScript", pkg: null,                  locked: true  },
    { label: "TypeScript", pkg: null,                  locked: true  },
    { label: "C",          pkg: null,                  locked: true  },
    { label: "C++",        pkg: null,                  locked: true  },
    { label: "Rust",       pkg: "tree-sitter-rust",    locked: false },
    { label: "Go",         pkg: "tree-sitter-go",      locked: false },
    { label: "Java",       pkg: "tree-sitter-java",    locked: false },
    { label: "Lua",        pkg: "tree-sitter-lua",     locked: false },
    { label: "Scala",      pkg: "tree-sitter-scala",   locked: false },
  ];
  const LOCKED_LANG_INDICES = ALL_LANG_ITEMS.map((l, i) => l.locked ? i : -1).filter(i => i !== -1);

  // Filled in step 4, consumed in the verification section
  let selectedLangPkgs = [];

  // --- Step-based wizard with ← back support ---
  let step = 1;

  while (step >= 1 && step <= 4) {

    // ─── Step 1: Workspace ───
    if (step === 1) {
      log(`  ${T.DOT} Step 1/4  Workspace`);
      log(`  ${T.SIDE}`);
      log(`  ${T.BRANCH} Stores indexed repos, graphs, and embeddings`);

      workspace =
        (await ask(`  ${T.SIDE}  Path [${WORKSPACE_DIR}]: `)).trim() || WORKSPACE_DIR;

      log(`  ${T.LAST} ${T.OK} ${workspace}`);
      log();
      step = 2;
      continue;
    }

    // ─── Step 2: LLM Provider ───
    if (step === 2) {
      log(`  ${T.DOT} Step 2/4  LLM Provider`);
      log(`  ${T.SIDE}`);
      log(`  ${T.BRANCH} For natural language queries & descriptions`);
      log(`  ${T.SIDE}  Use ↑↓ navigate, Enter confirm, ← back`);
      log(`  ${T.SIDE}`);

      if (existing.LLM_API_KEY) {
        log(`  ${T.SIDE}  Current: ${mask(existing.LLM_API_KEY)} → ${existing.LLM_BASE_URL || "?"}`);
        log(`  ${T.SIDE}`);
      }

      rl.close();
      const llmChoice = await selectMenu(llmOptions, `  ${T.SIDE}  `, 6, true);
      rl = createInterface({ input: process.stdin, output: process.stderr });
      ask = (q) => new Promise((resolve) => rl.question(q, resolve));

      if (llmChoice === -2) { log(); step = 1; continue; }
      if (llmChoice === -1) { rl.close(); return; }

      llmKey = existing.LLM_API_KEY || "";
      llmBaseUrl = existing.LLM_BASE_URL || "";
      llmModel = existing.LLM_MODEL || "";
      llmProviderName = "skipped";

      if (llmChoice >= 0 && llmChoice < 5) {
        const provider = llmProviders[llmChoice];
        llmBaseUrl = provider.url;
        llmModel = provider.model;
        llmProviderName = provider.name;

        log(`  ${T.SIDE}`);
        llmKey = (await ask(`  ${T.SIDE}  API Key (sk-...): `)).trim() || existing.LLM_API_KEY || "";

        if (llmKey) {
          const urlOverride = (await ask(`  ${T.SIDE}  Base URL [${llmBaseUrl}]: `)).trim();
          if (urlOverride) llmBaseUrl = urlOverride;
          const modelOverride = (await ask(`  ${T.SIDE}  Model [${llmModel}]: `)).trim();
          if (modelOverride) llmModel = modelOverride;
        }
      } else if (llmChoice === 5) {
        llmProviderName = "Custom";
        const defUrl = llmBaseUrl || existing.LLM_BASE_URL || "";
        const defModel = llmModel || existing.LLM_MODEL || "gpt-4o";
        const defKey = existing.LLM_API_KEY || "";
        log(`  ${T.SIDE}`);
        llmBaseUrl = (await ask(`  ${T.SIDE}  API Base URL${defUrl ? ` [${defUrl}]` : ""}: `)).trim() || defUrl;
        llmModel = (await ask(`  ${T.SIDE}  Model${defModel ? ` [${defModel}]` : ""}: `)).trim() || defModel;
        llmKey = (await ask(`  ${T.SIDE}  API Key${defKey ? ` [${mask(defKey)}]` : " (sk-...)"}: `)).trim() || defKey;
      }

      if (llmKey) {
        log(`  ${T.LAST} ${T.OK} ${llmProviderName} / ${llmModel}`);
      } else {
        log(`  ${T.LAST} ${T.WARN} Skipped (configure later in ${ENV_FILE})`);
      }
      log();
      step = 3;
      continue;
    }

    // ─── Step 3: Embedding Provider ───
    if (step === 3) {
      log(`  ${T.DOT} Step 3/4  Embedding Provider`);
      log(`  ${T.SIDE}`);
      log(`  ${T.BRANCH} For semantic code search`);
      log(`  ${T.SIDE}  Use ↑↓ navigate, Enter confirm, ← back`);
      log(`  ${T.SIDE}`);

      if (existing.DASHSCOPE_API_KEY || existing.EMBED_API_KEY) {
        const ek = existing.DASHSCOPE_API_KEY || existing.EMBED_API_KEY;
        log(`  ${T.SIDE}  Current: ${mask(ek)} → ${existing.DASHSCOPE_BASE_URL || existing.EMBED_BASE_URL || "?"}`);
        log(`  ${T.SIDE}`);
      }

      rl.close();
      const embedChoice = await selectMenu(embedOptions, `  ${T.SIDE}  `, 3, true);
      rl = createInterface({ input: process.stdin, output: process.stderr });
      ask = (q) => new Promise((resolve) => rl.question(q, resolve));

      if (embedChoice === -2) { log(); step = 2; continue; }
      if (embedChoice === -1) { rl.close(); return; }

      if (embedChoice >= 0 && embedChoice < 2) {
        // Reset before configuring a new provider
        embedKey = "";
        embedUrl = "";
        embedModel = "";
        embedKeyEnv = "DASHSCOPE_API_KEY";
        embedUrlEnv = "DASHSCOPE_BASE_URL";
        embedProviderName = "skipped";
        const ep = embedProvidersList[embedChoice];
        embedUrl = ep.url;
        embedModel = ep.model;
        embedKeyEnv = ep.keyEnv;
        embedUrlEnv = ep.urlEnv;
        embedProviderName = ep.name;

        log(`  ${T.SIDE}`);
        embedKey = (await ask(`  ${T.SIDE}  API Key: `)).trim() ||
          existing[embedKeyEnv] || existing.DASHSCOPE_API_KEY || "";

        if (embedKey) {
          const urlOverride = (await ask(`  ${T.SIDE}  Base URL [${embedUrl}]: `)).trim();
          if (urlOverride) embedUrl = urlOverride;
          const modelOverride = (await ask(`  ${T.SIDE}  Model [${embedModel}]: `)).trim();
          if (modelOverride) embedModel = modelOverride;
        }
      } else if (embedChoice === 2) {
        embedProviderName = "Custom";
        const defEmbedUrl = existing.EMBED_BASE_URL || existing.DASHSCOPE_BASE_URL || "";
        const defEmbedModel = existing.EMBED_MODEL || "text-embedding-3-small";
        const defEmbedKey = existing.EMBED_API_KEY || existing.DASHSCOPE_API_KEY || "";
        log(`  ${T.SIDE}`);
        embedUrl = (await ask(`  ${T.SIDE}  API Base URL${defEmbedUrl ? ` [${defEmbedUrl}]` : ""}: `)).trim() || defEmbedUrl;
        embedModel = (await ask(`  ${T.SIDE}  Model${defEmbedModel ? ` [${defEmbedModel}]` : ""}: `)).trim() || defEmbedModel;
        embedKey = (await ask(`  ${T.SIDE}  API Key${defEmbedKey ? ` [${mask(defEmbedKey)}]` : ""}: `)).trim() || defEmbedKey;
        embedKeyEnv = "EMBED_API_KEY";
        embedUrlEnv = "EMBED_BASE_URL";
      }

      if (embedKey) {
        log(`  ${T.LAST} ${T.OK} ${embedProviderName} / ${embedModel}`);
      } else {
        log(`  ${T.LAST} ${T.WARN} Skipped (configure later in ${ENV_FILE})`);
      }

      step = 4;
      continue;
    }

    // ─── Step 4: Language Support ───
    if (step === 4) {
      log(`  ${T.DOT} Step 4/4  Language Support`);
      log(`  ${T.SIDE}`);
      log(`  ${T.BRANCH} Dimmed items are core dependencies — always included`);
      log(`  ${T.BRANCH} Space to toggle additional languages, Enter to confirm`);
      log(`  ${T.SIDE}`);

      const langLabels = ALL_LANG_ITEMS.map(l => l.label);
      // Default: only core (locked) ones selected; optional start unchecked
      const defaultSelected = [...LOCKED_LANG_INDICES];

      rl.close();
      const langResult = await multiSelectMenu(langLabels, defaultSelected, `  ${T.SIDE}  `, true, LOCKED_LANG_INDICES);
      rl = createInterface({ input: process.stdin, output: process.stderr });
      ask = (q) => new Promise((resolve) => rl.question(q, resolve));

      if (langResult === -2) { log(); step = 3; continue; }
      if (langResult === null) { rl.close(); return; }

      // Only collect optional (non-locked) packages that the user checked
      selectedLangPkgs = langResult
        .filter(i => !ALL_LANG_ITEMS[i].locked && ALL_LANG_ITEMS[i].pkg)
        .map(i => ALL_LANG_ITEMS[i].pkg);

      const selectedOptionalLabels = langResult
        .filter(i => !ALL_LANG_ITEMS[i].locked)
        .map(i => ALL_LANG_ITEMS[i].label);

      if (selectedOptionalLabels.length > 0) {
        log(`  ${T.LAST} ${T.OK} + ${selectedOptionalLabels.join(" · ")}`);
      } else {
        log(`  ${T.LAST} ${T.OK} Core only`);
      }
      log();

      step = 5; // done — exit loop
      continue;
    }
  }

  rl.close();

  // --- Save config ---
  const config = {
    CGB_WORKSPACE: workspace,
    LLM_API_KEY: llmKey,
    LLM_BASE_URL: llmBaseUrl,
    LLM_MODEL: llmModel,
  };

  if (embedKey) {
    config[embedKeyEnv] = embedKey;
    config[embedUrlEnv] = embedUrl;
    if (embedModel) config.EMBED_MODEL = embedModel;
  }

  saveEnvFile(config);

  log();
  log(`  ${T.DOT} Configuration saved`);
  log(`  ${T.SIDE}`);
  log(`  ${T.BRANCH} File:      ${ENV_FILE}`);
  log(`  ${T.BRANCH} LLM:       ${llmKey ? `${llmProviderName} / ${llmModel}` : "not configured"}`);
  log(`  ${T.BRANCH} Embedding: ${embedKey ? `${embedProviderName} / ${embedModel}` : "not configured"}`);
  log(`  ${T.LAST} Workspace: ${workspace}`);
  log();

  // --- Verification ---
  log(`  ${T.DOT} Verification`);
  log(`  ${T.SIDE}`);

  // 1. Python
  if (!PYTHON_CMD) {
    log(`  ${T.BRANCH} ${T.FAIL} Python 3 not found`);
    log(`  ${T.LAST}   Install Python 3.10+ and re-run: terrain setup`);
    log();
    return;
  }
  log(`  ${T.BRANCH} ${T.OK} ${PYTHON_VER}`);

  // 2. Package — auto-install or upgrade
  const pip = findPip();
  // Build install target: base package + any optional language extras user selected
  const installTargets = [PYTHON_PACKAGE, ...selectedLangPkgs];
  const installDesc = selectedLangPkgs.length > 0
    ? `${PYTHON_PACKAGE} + ${selectedLangPkgs.join(", ")}`
    : PYTHON_PACKAGE;
  log(`  ${T.SIDE}  ${T.WORK} Installing ${installDesc} (force-reinstall)...`);
  if (pip) {
    try {
      execSync(
        [...pip, "install", "--prefer-binary", "--force-reinstall", "--upgrade", ...installTargets].map(s => `"${s}"`).join(" "),
        { stdio: "pipe", shell: true }
      );
    } catch { /* handled below */ }
  }

  if (pythonPackageInstalled()) {
    const ver = getPackageVersion();
    log(`  ${T.BRANCH} ${T.OK} ${PYTHON_PACKAGE} ${ver || ""}`);
    if (selectedLangPkgs.length > 0) {
      log(`  ${T.SIDE}       Language extras: ${selectedLangPkgs.join(", ")}`);
    }
  } else {
    log(`  ${T.BRANCH} ${T.FAIL} Package not installed`);
    log(`  ${T.LAST}   Run manually: pip install ${PYTHON_PACKAGE}`);
    log();
    return;
  }

  // 2b. Windows: ensure Python Scripts dir is on user PATH so `terrain` works
  if (IS_WIN && PYTHON_CMD) {
    try {
      const scriptsDir = execSync(
        `${PYTHON_CMD} -c "import sysconfig; print(sysconfig.get_path('scripts'))"`,
        { encoding: "utf-8", shell: true }
      ).trim();
      if (scriptsDir && existsSync(scriptsDir)) {
        const userPath = execSync('powershell -Command "[Environment]::GetEnvironmentVariable(\'Path\',\'User\')"', {
          encoding: "utf-8", shell: true
        }).trim();
        if (!userPath.toLowerCase().split(";").some(p => p.trim().toLowerCase() === scriptsDir.toLowerCase())) {
          const newPath = userPath ? `${userPath};${scriptsDir}` : scriptsDir;
          execSync(`setx PATH "${newPath}"`, { stdio: "pipe", shell: true });
          // Also update current process PATH so smoke test works
          process.env.Path = `${process.env.Path};${scriptsDir}`;
          log(`  ${T.BRANCH} ${T.OK} Added Python Scripts to user PATH: ${scriptsDir}`);
          log(`  ${T.SIDE}       (new PowerShell windows will pick this up automatically)`);
        }
      }
    } catch { /* non-critical, skip silently */ }
  }

  // 3. MCP server smoke test
  log(`  ${T.SIDE}  ${T.WORK} MCP server smoke test...`);

  const verified = await new Promise((resolve) => {
    const envVars = loadEnvFile();
    const mergedEnv = { ...process.env, ...envVars };
    if (!mergedEnv.CGB_WORKSPACE) mergedEnv.CGB_WORKSPACE = WORKSPACE_DIR;

    const child = spawn(PYTHON_CMD, ["-m", MODULE_PATH], {
      stdio: ["pipe", "pipe", "pipe"],
      env: mergedEnv,
      shell: IS_WIN,
    });

    let stdout = "";
    let resolved = false;

    const finish = (success, detail) => {
      if (resolved) return;
      resolved = true;
      try { child.kill(); } catch {}
      resolve({ success, detail });
    };

    const timer = setTimeout(() => finish(false, "Server did not respond within 15s"), 15000);

    child.stderr.on("data", () => {});

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
      const lines = stdout.split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("{")) continue;
        try {
          const msg = JSON.parse(trimmed);
          if (msg.result && msg.result.capabilities) {
            const toolsReq = JSON.stringify({
              jsonrpc: "2.0", id: 2, method: "tools/list", params: {},
            });
            child.stdin.write(toolsReq + "\n");
            stdout = "";
            return;
          }
          if (msg.result && msg.result.tools) {
            clearTimeout(timer);
            finish(true, `${msg.result.tools.length} tools`);
            return;
          }
        } catch { /* partial JSON */ }
      }
    });

    child.on("error", (err) => {
      clearTimeout(timer);
      finish(false, err.message);
    });

    child.on("exit", (code) => {
      clearTimeout(timer);
      if (!resolved) finish(false, `Server exited with code ${code}`);
    });

    const initReq = JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "setup-verify", version: "1.0.0" },
      },
    });
    child.stdin.write(initReq + "\n");
  });

  if (verified.success) {
    log(`  ${T.BRANCH} ${T.OK} MCP server (${verified.detail})`);
  } else {
    log(`  ${T.BRANCH} ${T.FAIL} MCP smoke test: ${verified.detail}`);
  }

  // 4. Claude Code registration
  if (commandExists("claude")) {
    try {
      try {
        execSync("claude mcp remove terrain", { stdio: "pipe", shell: true });
      } catch { /* not found */ }

      const addCmd = IS_WIN
        ? 'claude mcp add --scope user --transport stdio terrain -- cmd /c npx -y terrain-ai@latest --server'
        : 'claude mcp add --scope user --transport stdio terrain -- npx -y terrain-ai@latest --server';

      execSync(addCmd, { stdio: "pipe", shell: true });
      log(`  ${T.LAST} ${T.OK} Claude Code MCP registered (global)`);
    } catch {
      log(`  ${T.LAST} ${T.WARN} Claude Code auto-register failed`);
      log(`       Run manually:`);
      if (IS_WIN) {
        log(`       claude mcp add --scope user --transport stdio terrain -- cmd /c npx -y terrain-ai@latest --server`);
      } else {
        log(`       claude mcp add --scope user --transport stdio terrain -- npx -y terrain-ai@latest --server`);
      }
    }
  } else {
    log(`  ${T.LAST} ${T.WARN} Claude Code CLI not found`);
    log();
    log(`       Add to your MCP client config manually:`);
    log();
    log(`       {`);
    log(`         "mcpServers": {`);
    log(`           "terrain": {`);
    log(`             "command": "npx",`);
    log(`             "args": ["-y", "terrain-ai@latest", "--server"]`);
    log(`           }`);
    log(`         }`);
    log(`       }`);
  }

  // 5. Install skill commands to ~/.claude/commands/
  installSkills(log);

  log();
  log(`  ${T.DOT} Setup complete`);
  log(`  ${T.SIDE}`);
  log(`  ${T.BRANCH} Run  terrain index <path>  to index a codebase`);

  // Show which language parsers are installed
  const optionalItems   = ALL_LANG_ITEMS.filter(l => !l.locked);
  const installedExtras = optionalItems.filter(l => selectedLangPkgs.includes(l.pkg)).map(l => l.label);
  const missingExtras   = optionalItems.filter(l => !selectedLangPkgs.includes(l.pkg)).map(l => l.label);
  const coreList = "Python · JS · TS · C · C++";
  const extraList = installedExtras.length > 0 ? " · " + installedExtras.join(" · ") : "";
  log(`  ${T.BRANCH} Parsers: ${coreList}${extraList}`);

  if (missingExtras.length > 0) {
    log(`  ${T.LAST} To add more languages (${missingExtras.join(", ")}), re-run:`);
    log(`         terrain setup`);
  } else {
    log(`  ${T.LAST} All language parsers installed`);
  }
  log();
}

// ---------------------------------------------------------------------------
// Install skill commands to ~/.claude/commands/
// ---------------------------------------------------------------------------

function installSkills(log) {
  const __dirname = dirname(fileURLToPath(import.meta.url));
  const srcDir = join(__dirname, "..", "commands");
  const targetDir = join(homedir(), ".claude", "commands");

  if (!existsSync(srcDir)) {
    // Running from development or commands dir not bundled
    if (log) log(`  ${T.BRANCH} ${T.WARN} Skill files not found in package`);
    return;
  }

  const skills = readdirSync(srcDir).filter(f => f.endsWith(".md"));
  if (skills.length === 0) return;

  try {
    mkdirSync(targetDir, { recursive: true });

    let installed = 0;
    for (const file of skills) {
      const src = join(srcDir, file);
      const dest = join(targetDir, file);
      copyFileSync(src, dest);
      installed++;
    }

    if (log) {
      log();
      log(`  ${T.DOT} Skills installed`);
      log(`  ${T.SIDE}`);
      for (let i = 0; i < skills.length; i++) {
        const name = skills[i].replace(".md", "");
        const prefix = i < skills.length - 1 ? T.BRANCH : T.LAST;
        log(`  ${prefix} /${name}`);
      }
    }
  } catch (err) {
    if (log) {
      log();
      log(`  ${T.DOT} ${T.WARN} Skill installation failed: ${err.message}`);
      log(`       Copy manually from: ${srcDir}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Start MCP server
// ---------------------------------------------------------------------------

function runServer(cmd, args) {
  const envVars = loadEnvFile();
  const mergedEnv = { ...process.env, ...envVars };

  if (!mergedEnv.CGB_WORKSPACE) {
    mergedEnv.CGB_WORKSPACE = WORKSPACE_DIR;
  }

  // Force unbuffered stdout/stderr so MCP JSON-RPC responses are flushed
  // immediately through the multi-layer pipe chain on Windows
  // (MCP client -> cmd.exe -> npx -> node -> cmd.exe -> python).
  mergedEnv.PYTHONUNBUFFERED = "1";

  const child = spawn(cmd, args, {
    stdio: "inherit",
    env: mergedEnv,
    shell: IS_WIN,
  });

  child.on("error", (err) => {
    process.stderr.write(`Failed to start MCP server: ${err.message}\n`);
    process.exit(1);
  });

  child.on("exit", (code) => {
    process.exit(code ?? 0);
  });
}

function autoInstallAndStart(extraArgs) {
  const pip = findPip();
  if (!pip) {
    process.stderr.write(
      `terrain requires Python 3.10+ with pip.\n\n` +
        (PYTHON_CMD
          ? `Python found (${PYTHON_CMD}) but pip is not available.\n\n`
          : `Python 3 not found on PATH.\n\n`) +
        `Please install Python 3.10+ first, then run:\n` +
        `  terrain server\n`
    );
    process.exit(1);
  }

  // Auto-install includes all language extras so the server can index any repo
  const autoInstallTarget = `${PYTHON_PACKAGE}[treesitter-full]`;
  process.stderr.write(`Installing ${autoInstallTarget}...\n`);

  try {
    execSync(
      [...pip, "install", "--prefer-binary", "--force-reinstall", "--upgrade", autoInstallTarget].map(s => `"${s}"`).join(" "),
      { stdio: "inherit", shell: true }
    );
  } catch (err) {
    process.stderr.write(
      `\nFailed to install ${autoInstallTarget}.\n` +
        `Try manually: ${pip.join(" ")} install "${autoInstallTarget}"\n`
    );
    process.exit(1);
  }

  if (!pythonPackageInstalled()) {
    process.stderr.write(
      `\nInstallation completed but package not importable.\n` +
        `Try manually: ${pip.join(" ")} install "${autoInstallTarget}"\n`
    );
    process.exit(1);
  }

  process.stderr.write(`${autoInstallTarget} installed successfully.\n`);

  // Windows: ensure Python Scripts dir is on user PATH
  if (IS_WIN && PYTHON_CMD) {
    try {
      const scriptsDir = execSync(
        `${PYTHON_CMD} -c "import sysconfig; print(sysconfig.get_path('scripts'))"`,
        { encoding: "utf-8", shell: true }
      ).trim();
      if (scriptsDir && existsSync(scriptsDir)) {
        const userPath = execSync('powershell -Command "[Environment]::GetEnvironmentVariable(\'Path\',\'User\')"', {
          encoding: "utf-8", shell: true
        }).trim();
        if (!userPath.toLowerCase().split(";").some(p => p.trim().toLowerCase() === scriptsDir.toLowerCase())) {
          const newPath = userPath ? `${userPath};${scriptsDir}` : scriptsDir;
          execSync(`setx PATH "${newPath}"`, { stdio: "pipe", shell: true });
          process.env.Path = `${process.env.Path};${scriptsDir}`;
        }
      }
    } catch { /* non-critical */ }
  }

  runServer(PYTHON_CMD, ["-m", MODULE_PATH]);
}

// ---------------------------------------------------------------------------
// Uninstall
// ---------------------------------------------------------------------------

async function runUninstall() {
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  const ask = (q) => new Promise((resolve) => rl.question(q, resolve));
  const log = (msg = "") => process.stderr.write(msg + "\n");

  log();
  log(box("terrain  Uninstall"));
  log();

  const pip = findPip();
  const hasPythonPkg = pythonPackageInstalled();
  const hasWorkspace = existsSync(WORKSPACE_DIR);
  const hasEnv = existsSync(ENV_FILE);

  let hasClaudeConfig = false;
  try {
    const mcpList = execFileSync("claude", ["mcp", "list"], { stdio: "pipe" }).toString();
    hasClaudeConfig = mcpList.includes("terrain");
  } catch { /* claude CLI not available */ }

  // Detect installed skill files
  const SKILL_NAMES = ["ask.md", "code-gen.md", "trace.md"];
  const skillDir = join(homedir(), ".claude", "commands");
  const installedSkills = SKILL_NAMES.filter(f => existsSync(join(skillDir, f)));

  log(`  ${T.DOT} Components detected`);
  log(`  ${T.SIDE}`);
  if (hasPythonPkg)    log(`  ${T.BRANCH} Python package:  terrain`);
  else                 log(`  ${T.BRANCH} Python package:  (not installed)`);
  if (hasWorkspace)    log(`  ${T.BRANCH} Workspace data:  ${WORKSPACE_DIR}`);
  else                 log(`  ${T.BRANCH} Workspace data:  (not found)`);
  if (hasEnv)          log(`  ${T.BRANCH} Config file:     ${ENV_FILE}`);
  if (hasClaudeConfig) log(`  ${T.BRANCH} Claude Code MCP: registered`);
  if (installedSkills.length > 0) log(`  ${T.BRANCH} Skill commands:  ${installedSkills.map(f => "/" + f.replace(".md", "")).join(", ")}`);
  log(`  ${T.LAST}`);
  log();

  const answer = (await ask("  Proceed with uninstall? [y/N]: ")).trim().toLowerCase();
  rl.close();

  if (answer !== "y" && answer !== "yes") {
    log("\n  Uninstall cancelled.\n");
    process.exit(0);
  }

  log();
  log(`  ${T.DOT} Removing`);
  log(`  ${T.SIDE}`);

  // Claude Code MCP entry
  if (hasClaudeConfig) {
    try {
      execSync("claude mcp remove terrain", { stdio: "pipe", shell: true });
      log(`  ${T.BRANCH} ${T.OK} Claude Code MCP entry`);
    } catch {
      log(`  ${T.BRANCH} ${T.WARN} Claude Code MCP entry (manual removal needed)`);
    }
  }

  // Python package
  if (hasPythonPkg && pip) {
    try {
      execSync(
        [...pip, "uninstall", "-y", PYTHON_PACKAGE].map(s => `"${s}"`).join(" "),
        { stdio: "pipe", shell: true }
      );
      log(`  ${T.BRANCH} ${T.OK} Python package`);
    } catch {
      log(`  ${T.BRANCH} ${T.WARN} Python package (try: pip uninstall terrain)`);
    }
  }

  // Workspace data
  if (hasWorkspace) {
    try {
      rmSync(WORKSPACE_DIR, { recursive: true, force: true });
      log(`  ${T.BRANCH} ${T.OK} Workspace data`);
    } catch (err) {
      log(`  ${T.BRANCH} ${T.WARN} Workspace: ${err.message}`);
    }
  }

  // Skill command files
  if (installedSkills.length > 0) {
    let removed = 0;
    for (const file of installedSkills) {
      try {
        rmSync(join(skillDir, file), { force: true });
        removed++;
      } catch { /* best effort */ }
    }
    if (removed > 0) {
      log(`  ${T.BRANCH} ${T.OK} Skill commands (${removed} files)`);
    }
  }

  // npx cache
  log(`  ${T.SIDE}  ${T.WORK} Clearing npx cache...`);
  await clearNpxCache();
  log(`  ${T.LAST} ${T.OK} npx cache`);

  log();
  log(`  ${T.DOT} Uninstall complete`);
  log();
}

// ---------------------------------------------------------------------------
// Move workspace
// ---------------------------------------------------------------------------

async function runMove() {
  let rl = createInterface({ input: process.stdin, output: process.stderr });
  let ask = (q) => new Promise((resolve) => rl.question(q, resolve));
  const log = (msg = "") => process.stderr.write(msg + "\n");

  log();
  log(box("terrain  Move Workspace"));
  log();

  // --- Detect current workspace ---
  const existing = loadEnvFile();
  const currentWorkspace = existing.CGB_WORKSPACE || WORKSPACE_DIR;

  if (!existsSync(currentWorkspace)) {
    log(`  ${T.WARN} Current workspace does not exist: ${currentWorkspace}`);
    log(`  Run terrain setup first.\n`);
    rl.close();
    process.exit(1);
  }

  // Calculate workspace size for display
  let sizeLabel = "";
  try {
    const output = IS_WIN
      ? execSync(`powershell -c "(Get-ChildItem -Recurse '${currentWorkspace}' | Measure-Object -Property Length -Sum).Sum"`, { stdio: "pipe", shell: true }).toString().trim()
      : execSync(`du -sh "${currentWorkspace}" 2>/dev/null`, { stdio: "pipe", shell: true }).toString().trim();
    if (IS_WIN) {
      const bytes = parseInt(output, 10);
      if (!isNaN(bytes)) {
        if (bytes > 1e9) sizeLabel = `${(bytes / 1e9).toFixed(1)} GB`;
        else if (bytes > 1e6) sizeLabel = `${(bytes / 1e6).toFixed(1)} MB`;
        else sizeLabel = `${(bytes / 1e3).toFixed(0)} KB`;
      }
    } else {
      sizeLabel = output.split(/\s/)[0];
    }
  } catch { /* ignore */ }

  // Count indexed repos
  let repoCount = 0;
  try {
    const repoDir = join(currentWorkspace, "repos");
    if (existsSync(repoDir)) {
      repoCount = readdirSync(repoDir).filter(f => {
        try { return statSync(join(repoDir, f)).isDirectory(); } catch { return false; }
      }).length;
    }
  } catch { /* ignore */ }

  // --- Step-based wizard with ← back support ---
  let newWorkspace = "";
  let step = 1;

  while (step >= 1 && step <= 3) {

    // ─── Step 1: Show current workspace info ───
    if (step === 1) {
      log(`  ${T.DOT} Step 1/3  Current Workspace`);
      log(`  ${T.SIDE}`);
      log(`  ${T.BRANCH} Path:  ${currentWorkspace}`);
      if (sizeLabel) {
        log(`  ${T.BRANCH} Size:  ${sizeLabel}`);
      }
      log(`  ${T.LAST} Repos: ${repoCount > 0 ? `${repoCount} indexed` : "none"}`);
      log();
      step = 2;
      continue;
    }

    // ─── Step 2: Choose destination ───
    if (step === 2) {
      log(`  ${T.DOT} Step 2/3  New Location`);
      log(`  ${T.SIDE}`);
      log(`  ${T.BRANCH} All repos, graphs, and embeddings will be moved`);
      log(`  ${T.SIDE}`);

      const newPath = (await ask(`  ${T.SIDE}  Path: `)).trim();

      if (!newPath) {
        log(`  ${T.LAST} ${T.WARN} No path entered. Aborted.\n`);
        rl.close();
        return;
      }

      // Resolve to absolute path
      const { resolve: resolvePath } = await import("node:path");
      newWorkspace = resolvePath(newPath.replace(/^~(?=\/|$)/, homedir()));

      if (newWorkspace === currentWorkspace) {
        log(`  ${T.LAST} ${T.WARN} Same as current workspace, nothing to do.\n`);
        rl.close();
        return;
      }

      if (existsSync(newWorkspace)) {
        log(`  ${T.SIDE}`);
        log(`  ${T.SIDE}  ${T.WARN} Target already exists: ${newWorkspace}`);

        rl.close();
        const mergeChoice = await selectMenu(
          ["Merge into existing directory", "Cancel"],
          `  ${T.SIDE}  `, 0, true,
        );
        rl = createInterface({ input: process.stdin, output: process.stderr });
        ask = (q) => new Promise((resolve) => rl.question(q, resolve));

        if (mergeChoice === -2) { log(); step = 1; continue; }
        if (mergeChoice === -1 || mergeChoice === 1) {
          log(`  ${T.LAST} Aborted.\n`);
          rl.close();
          return;
        }
      }

      log(`  ${T.LAST} ${T.OK} ${newWorkspace}`);
      log();
      step = 3;
      continue;
    }

    // ─── Step 3: Confirm and execute ───
    if (step === 3) {
      log(`  ${T.DOT} Step 3/3  Confirm`);
      log(`  ${T.SIDE}`);
      log(`  ${T.BRANCH} From:   ${currentWorkspace}`);
      log(`  ${T.BRANCH} To:     ${newWorkspace}`);
      log(`  ${T.LAST} Action: move all data, update config, delete old`);
      log();

      rl.close();
      const confirmChoice = await selectMenu(
        ["Proceed", "Cancel"],
        "  ", 0, true,
      );
      rl = createInterface({ input: process.stdin, output: process.stderr });
      ask = (q) => new Promise((resolve) => rl.question(q, resolve));

      if (confirmChoice === -2) { log(); step = 2; continue; }
      if (confirmChoice === -1 || confirmChoice === 1) {
        log("\n  Aborted.\n");
        rl.close();
        return;
      }

      // --- Execute move ---
      log();
      log(`  ${T.DOT} Moving workspace`);
      log(`  ${T.SIDE}`);

      // Try rename first (fast, same-volume)
      let moved = false;
      if (!existsSync(newWorkspace)) {
        try {
          mkdirSync(dirname(newWorkspace), { recursive: true });
          renameSync(currentWorkspace, newWorkspace);
          moved = true;
          log(`  ${T.BRANCH} ${T.OK} Renamed -> ${newWorkspace}`);
        } catch {
          // Cross-device or locked — fall through to copy
        }
      }

      // Fall back to copy + delete
      if (!moved) {
        try {
          mkdirSync(newWorkspace, { recursive: true });
          cpSync(currentWorkspace, newWorkspace, { recursive: true });
          moved = true;
          log(`  ${T.BRANCH} ${T.OK} Copied -> ${newWorkspace}`);
        } catch (err) {
          log(`  ${T.BRANCH} ${T.WARN} Copy failed: ${err.message}`);
          log(`\n  Migration aborted. Your original workspace is untouched.\n`);
          rl.close();
          process.exit(1);
        }

        // Delete old directory (best-effort)
        try {
          rmSync(currentWorkspace, { recursive: true, force: true });
          log(`  ${T.BRANCH} ${T.OK} Deleted ${currentWorkspace}`);
        } catch {
          if (IS_WIN) {
            try {
              execSync(`rd /s /q "${currentWorkspace}"`, { stdio: "pipe", shell: true });
              log(`  ${T.BRANCH} ${T.OK} Deleted ${currentWorkspace}`);
            } catch { /* still locked */ }
          }
          if (existsSync(currentWorkspace)) {
            log(`  ${T.BRANCH} ${T.WARN} Could not remove ${currentWorkspace} (may be locked). Please delete it manually.`);
          }
        }
      }

      // Update .env
      if (moved) {
        const envVars = loadEnvFile();
        envVars.CGB_WORKSPACE = newWorkspace;

        if (currentWorkspace === WORKSPACE_DIR) {
          mkdirSync(WORKSPACE_DIR, { recursive: true });
          const movedEnv = join(newWorkspace, ".env");
          if (existsSync(movedEnv)) {
            copyFileSync(movedEnv, ENV_FILE);
          }
        }

        saveEnvFile(envVars);
        log(`  ${T.BRANCH} ${T.OK} Updated config: CGB_WORKSPACE=${newWorkspace}`);
      }

      log(`  ${T.LAST} ${T.OK} Done`);
      log();
      break;
    }
  }

  rl.close();
}

function startServer(extraArgs = []) {
  // Ensure skills are installed (silent, no output on stdio — MCP uses it)
  installSkills(null);

  // Show notice if a background update completed since last startup
  showPendingUpdateNotice();

  // Launch background auto-updater (detached, never blocks)
  backgroundAutoUpdate();

  if (pythonPackageInstalled()) {
    runServer(PYTHON_CMD, ["-m", MODULE_PATH]);
  } else if (commandExists("uvx")) {
    runServer("uvx", [PYTHON_PACKAGE, ...extraArgs]);
  } else if (commandExists("uv")) {
    runServer("uv", ["tool", "run", PYTHON_PACKAGE, ...extraArgs]);
  } else if (commandExists("pipx")) {
    runServer("pipx", ["run", PYTHON_PACKAGE, ...extraArgs]);
  } else {
    autoInstallAndStart(extraArgs);
  }
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------

async function runUpdate() {
  const log = (msg = "") => process.stderr.write(msg + "\n");

  log();
  log(box("terrain  Update"));
  log();

  if (isAutoUpdateDisabled()) {
    log(`  ${T.WARN} Auto-updater is disabled (DISABLE_AUTOUPDATER=1)`);
    log(`  ${T.LAST} Remove DISABLE_AUTOUPDATER from .env or environment to re-enable`);
    log();
  }

  // ---- Gather version info ----
  log(`  ${T.BRANCH} Checking for updates${T.WORK}`);

  const localNpm  = getLocalNpmVersion();
  const latestNpm = getLatestNpmVersion();
  const localPy   = getPackageVersion();
  const latestPy  = getLatestPypiVersion();

  const npmOutdated = localNpm && latestNpm && semverCompare(latestNpm, localNpm) > 0;
  const pyOutdated  = localPy  && latestPy  && semverCompare(latestPy, localPy) > 0;

  log(`  ${T.BRANCH} CLI (npm):    ${localNpm || "not installed"} ${npmOutdated ? `→ ${latestNpm}` : latestNpm ? `${T.OK} latest` : ""}`);
  log(`  ${T.BRANCH} Python (pip): ${localPy  || "not installed"} ${pyOutdated  ? `→ ${latestPy}`  : latestPy  ? `${T.OK} latest` : ""}`);

  if (!npmOutdated && !pyOutdated) {
    log();
    log(`  ${T.LAST} ${T.OK} Everything is up to date!`);
    log();
    recordUpdateCheck();
    return;
  }

  // ---- Update npm package ----
  if (npmOutdated) {
    log();
    log(`  ${T.BRANCH} Updating CLI: ${localNpm} → ${latestNpm}${T.WORK}`);
    try {
      execSync("npm install -g terrain-ai@latest", {
        stdio: "inherit", shell: true, timeout: 120_000,
      });
      log(`  ${T.PIPE} ${T.OK} CLI updated to ${latestNpm}`);
    } catch {
      log(`  ${T.PIPE} ${T.FAIL} CLI update failed. Try manually:`);
      log(`  ${T.PIPE}   npm install -g terrain-ai@latest`);
    }
  }

  // ---- Update Python package ----
  if (pyOutdated) {
    log();
    log(`  ${T.BRANCH} Updating Python package: ${localPy} → ${latestPy}${T.WORK}`);
    const pip = findPip();
    if (pip) {
      try {
        const target = `${PYTHON_PACKAGE}[treesitter-full]`;
        execSync(
          [...pip, "install", "--upgrade", "--prefer-binary", target].map(s => `"${s}"`).join(" "),
          { stdio: "inherit", shell: true, timeout: 300_000 }
        );
        const newVer = getPackageVersion();
        log(`  ${T.PIPE} ${T.OK} Python package updated to ${newVer || latestPy}`);
      } catch {
        log(`  ${T.PIPE} ${T.FAIL} Python update failed. Try manually:`);
        log(`  ${T.PIPE}   ${pip.join(" ")} install --upgrade terrain-ai`);
      }
    } else {
      log(`  ${T.PIPE} ${T.WARN} pip not found — cannot update Python package`);
      log(`  ${T.PIPE}   Install pip and run: pip install --upgrade terrain-ai`);
    }
  }

  recordUpdateCheck();
  // Clear any pending marker since we just did a manual update
  try { rmSync(UPDATE_PENDING_FILE, { force: true }); } catch {}

  log();
  log(`  ${T.LAST} ${T.OK} Done`);
  log();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const args = process.argv.slice(2);
const mode = args[0];

// Normalise: accept both "--setup" and "setup" style
const cmd = (mode || "").replace(/^--/, "");

if (cmd === "setup") {
  runSetup();
} else if (cmd === "server" || cmd === "pip" || cmd === "python") {
  if (cmd === "pip" || cmd === "python") {
    if (!PYTHON_CMD || !pythonPackageInstalled()) {
      process.stderr.write(
        `Error: Python package '${PYTHON_PACKAGE}' is not installed.\n` +
          `Run: pip install ${PYTHON_PACKAGE}\n`
      );
      process.exit(1);
    }
    runServer(PYTHON_CMD, ["-m", MODULE_PATH]);
  } else {
    startServer(args.slice(1));
  }
} else if (cmd === "uninstall") {
  runUninstall();
} else if (cmd === "move") {
  runMove();
} else if (cmd === "update" || cmd === "upgrade") {
  runUpdate();
} else if (cmd === "help" || cmd === "h" || mode === "-h") {
  const log = (msg) => process.stderr.write(msg + "\n");
  log("");
  log(box("terrain"));
  log("");
  log("  Usage:");
  log("");
  log("    terrain              Interactive setup wizard");
  log("    terrain server       Start MCP server");
  log("    terrain setup        Re-run setup wizard");
  log("    terrain update       Check & install updates");
  log("    terrain move         Move workspace to a new location");
  log("    terrain uninstall    Completely uninstall");
  log("    terrain help         Show this help");
  log("");
  log(`  Config: ${ENV_FILE}`);
  log("");
  log("  Auto-update:");
  log("    Background updates run automatically every 4 hours.");
  log("    Updates download silently and take effect on next startup.");
  log("    To disable: set DISABLE_AUTOUPDATER=1 in .env or environment.");
  log("");
} else {
  if (!existsSync(ENV_FILE)) {
    runSetup();
  } else {
    startServer(args);
  }
}
