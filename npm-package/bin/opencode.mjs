// opencode client support.
//
// opencode (https://opencode.ai) stores its MCP config in
// $XDG_CONFIG_HOME/opencode/opencode.json (or .jsonc) and loads slash
// commands from $XDG_CONFIG_HOME/opencode/command/*.md.
// Unlike `claude mcp add`, `opencode mcp add` is a TUI, so setup writes to
// the JSON file directly.

import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync, copyFileSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const IS_WIN = platform() === "win32";
const SKILL_NAMES = ["ask.md", "code-gen.md", "research.md", "trace.md"];

export function getOpencodeConfigDir(env = process.env, home = homedir()) {
  const xdg = env.XDG_CONFIG_HOME;
  const base = xdg && xdg.length > 0 ? xdg : join(home, ".config");
  return join(base, "opencode");
}

export function getOpencodeConfigPath(env = process.env, home = homedir()) {
  const dir = getOpencodeConfigDir(env, home);
  const jsonc = join(dir, "opencode.jsonc");
  if (existsSync(jsonc)) return jsonc;
  return join(dir, "opencode.json");
}

export function getOpencodeCommandDir(env = process.env, home = homedir()) {
  // opencode uses singular `command/`, not `commands/` like Claude.
  return join(getOpencodeConfigDir(env, home), "command");
}

export function readOpencodeConfig(env = process.env, home = homedir()) {
  const path = getOpencodeConfigPath(env, home);
  if (!existsSync(path)) {
    return { path, cfg: {}, existed: false };
  }
  const raw = readFileSync(path, "utf-8");
  try {
    return { path, cfg: JSON.parse(raw), existed: true };
  } catch (err) {
    // Likely JSONC or malformed JSON — surface to caller so it falls back to
    // manual-instructions mode rather than overwriting the user's file.
    const e = new Error(
      `Cannot parse ${path}: ${err.message}. ` +
        `Edit it manually to add the terrain MCP block.`
    );
    e.code = "EOPENCODE_UNPARSEABLE";
    throw e;
  }
}

export function writeOpencodeConfig(cfg, path) {
  const dir = dirname(path);
  mkdirSync(dir, { recursive: true });
  if (!cfg.$schema) cfg.$schema = "https://opencode.ai/config.json";
  writeFileSync(path, JSON.stringify(cfg, null, 2) + "\n", "utf-8");
}

export function opencodeMcpBlock({ isWin = IS_WIN } = {}) {
  const command = isWin
    ? ["cmd", "/c", "npx", "-y", "terrain-ai@latest", "--server"]
    : ["npx", "-y", "terrain-ai@latest", "--server"];
  return {
    type: "local",
    command,
    enabled: true,
  };
}

export function registerOpencodeMcp(opts = {}) {
  const { env = process.env, home = homedir(), isWin = IS_WIN } = opts;
  const { path, cfg } = readOpencodeConfig(env, home);
  if (!cfg.mcp || typeof cfg.mcp !== "object") cfg.mcp = {};
  cfg.mcp.terrain = opencodeMcpBlock({ isWin });
  writeOpencodeConfig(cfg, path);
  return path;
}

export function unregisterOpencodeMcp(opts = {}) {
  const { env = process.env, home = homedir() } = opts;
  let result;
  try {
    result = readOpencodeConfig(env, home);
  } catch {
    return false;
  }
  if (!result.existed) return false;
  const { path, cfg } = result;
  if (!cfg.mcp || typeof cfg.mcp !== "object" || !cfg.mcp.terrain) {
    return false;
  }
  delete cfg.mcp.terrain;
  if (Object.keys(cfg.mcp).length === 0) delete cfg.mcp;
  writeOpencodeConfig(cfg, path);
  return true;
}

export function installOpencodeSkills(opts = {}) {
  const { env = process.env, home = homedir(), srcDir } = opts;
  const __oc_dirname = dirname(fileURLToPath(import.meta.url));
  // Try multiple candidate paths (npm package layout vs root dev wrapper)
  const candidates = [
    join(__oc_dirname, "..", "commands"),
    join(__oc_dirname, "..", "..", "npm-package", "commands"),
  ];
  const packageSrcDir = srcDir || candidates.find(d => existsSync(d));
  const targetDir = getOpencodeCommandDir(env, home);

  if (!packageSrcDir || !existsSync(packageSrcDir)) {
    return { installed: [], skills: [], targetDir, missing: true };
  }

  const skills = readdirSync(packageSrcDir).filter(f => f.endsWith(".md"));
  if (skills.length === 0) return { installed: [], skills: [], targetDir };

  mkdirSync(targetDir, { recursive: true });
  const installed = [];
  for (const file of skills) {
    copyFileSync(join(packageSrcDir, file), join(targetDir, file));
    installed.push(file);
  }
  return { installed, skills, targetDir };
}

export function detectOpencodeState(opts = {}) {
  const { env = process.env, home = homedir(), commandExists } = opts;
  const hasCli = typeof commandExists === "function" ? commandExists("opencode") : false;
  let hasMcp = false;
  try {
    const { cfg, existed } = readOpencodeConfig(env, home);
    hasMcp = Boolean(existed && cfg.mcp && cfg.mcp.terrain);
  } catch {
    // unparseable config — treat as absent
  }
  const cmdDir = getOpencodeCommandDir(env, home);
  const installedSkills = SKILL_NAMES.filter(f => existsSync(join(cmdDir, f)));
  return { hasCli, hasMcp, installedSkills, cmdDir };
}

export const OPENCODE_SKILL_NAMES = SKILL_NAMES;
