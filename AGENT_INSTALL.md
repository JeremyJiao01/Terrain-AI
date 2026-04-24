# terrain-ai Agent Installation Guide

> You are an AI agent. Read every block top-to-bottom and execute the commands.
> Do not skip blocks. Do not proceed past a block that ends in "STOP".

---

## Block 1 — Detect Operating System

Run the appropriate command and store the result as PLATFORM:

**Mac/Linux:**
```bash
uname -s
```
- Output contains `Darwin` → PLATFORM = `mac`
- Output contains `Linux` → PLATFORM = `linux`

**Windows** (run in PowerShell or cmd):
```
echo %OS%
```
- Output contains `Windows` → PLATFORM = `windows`

Use PLATFORM in every subsequent block to choose the correct commands.

---

## Block 2 — Verify Python 3.11.x

terrain-ai requires **exactly Python 3.11.x**. Versions 3.10, 3.12, and above are not supported. STOP if you cannot find Python 3.11.

**Check (Mac/Linux):**
```bash
python3.11 --version
```

**Check (Windows):**
```
py -3.11 --version
```

Expected output: `Python 3.11.x` where x is any patch version.

**If Python 3.11.x is found** → continue to Block 3.

**If Python 3.11 is not found or the version is wrong** → install it using the instructions below, then re-run the check. Do NOT continue with any other Python version.

| PLATFORM | Installation command |
|----------|---------------------|
| `mac`    | `brew install python@3.11` |
| `linux`  | `sudo apt install python3.11` or `sudo dnf install python3.11` |
| `windows`| Download the installer from `https://www.python.org/downloads/release/python-3119/` — check **"Add Python to PATH"** before installing. |

After installing, re-run the version check before continuing. If the version is still not 3.11.x, STOP and ask the user to resolve the Python installation manually.

---

## Block 3 — Install terrain-ai Python Package

Try the following commands in order. Stop at the first one that succeeds (exit code 0). If all four fail, STOP and report the full error output to the user, then suggest checking firewall or proxy settings.

**Mac/Linux — try in order:**
```bash
# Attempt 1: official PyPI
python3.11 -m pip install terrain-ai

# Attempt 2: Tsinghua mirror
python3.11 -m pip install terrain-ai -i https://pypi.tuna.tsinghua.edu.cn/simple

# Attempt 3: Aliyun mirror
python3.11 -m pip install terrain-ai -i https://mirrors.aliyun.com/pypi/simple

# Attempt 4: Douban mirror
python3.11 -m pip install terrain-ai -i https://pypi.douban.com/simple
```

**Windows — try in order:**
```
py -3.11 -m pip install terrain-ai
py -3.11 -m pip install terrain-ai -i https://pypi.tuna.tsinghua.edu.cn/simple
py -3.11 -m pip install terrain-ai -i https://mirrors.aliyun.com/pypi/simple
py -3.11 -m pip install terrain-ai -i https://pypi.douban.com/simple
```

After a successful install, verify:

**Mac/Linux:** `python3.11 -c "import terrain; print('ok')"`
**Windows:** `py -3.11 -c "import terrain; print('ok')"`

Expected output: `ok`. If this fails, try the next mirror source.

---

## Block 4 — Check for Existing API Keys

Check if `~/.terrain/.env` already contains keys.

**Mac/Linux:**
```bash
cat ~/.terrain/.env 2>/dev/null
```

**Windows:**
```
type "%USERPROFILE%\.terrain\.env" 2>nul
```

**If the file exists and contains `LLM_API_KEY`** → the keys are already configured. Skip to Block 5.

**If the file is missing or `LLM_API_KEY` is absent** → ask the user the following questions one at a time:

1. Which LLM provider are you using? (OpenAI / Anthropic / Gemini / other — for "other", ask for the base URL)
2. What is your LLM API key?
3. What model name should be used? (e.g. `gpt-4o`, `claude-opus-4-6`, `gemini-2.0-flash`)
4. Do you use a separate embedding provider? If yes: which one, what is the API key, and what model name?

Store the answers as variables. Do NOT write them to disk yet — proceed to Block 4.5 to validate them first.

---

## Block 4.5 — Validate API Keys

Test each key before writing it to disk. Use the exact request format below. If a test fails, show the HTTP status code and error body, ask the user to re-enter the key, and retry. Only write keys to disk after both tests pass — or after the user explicitly says "skip".

### LLM test — minimal chat completion

Send this HTTP request (replace placeholders with collected values):

```
POST {LLM_BASE_URL}/chat/completions
Authorization: Bearer {LLM_API_KEY}
Content-Type: application/json

{
  "model": "{LLM_MODEL}",
  "messages": [{"role": "user", "content": "Reply with OK"}],
  "max_tokens": 32,
  "temperature": 1.0,
  "top_p": 0.9,
  "stream": false
}
```

Default `LLM_BASE_URL` if not specified: `https://api.openai.com/v1`

**Success:** HTTP 200 and the response JSON contains `choices[0].message.content`.
**Failure:** Report the status code and the full response body. Ask the user to re-enter the key and retry.

### Embedding test — minimal single-vector request

```
POST {EMBEDDING_BASE_URL}/embeddings
Authorization: Bearer {EMBEDDING_API_KEY}
Content-Type: application/json

{
  "model": "{EMBEDDING_MODEL}",
  "input": ["hello"]
}
```

Default `EMBEDDING_BASE_URL`: same as `LLM_BASE_URL` if using the same provider.
Default `EMBEDDING_API_KEY`: same as `LLM_API_KEY` if using the same provider.

**Success:** HTTP 200 and `data[0].embedding` is a non-empty array.
**Failure:** Report the status code and the full response body. Ask the user to re-enter the key and retry.

### Write keys to disk

After both tests pass (or user skips), write only the validated keys to `~/.terrain/.env`:

**Mac/Linux:**
```bash
mkdir -p ~/.terrain
cat > ~/.terrain/.env << 'EOF'
LLM_API_KEY={LLM_API_KEY}
LLM_BASE_URL={LLM_BASE_URL}
LLM_MODEL={LLM_MODEL}
EMBEDDING_API_KEY={EMBEDDING_API_KEY}
EMBEDDING_BASE_URL={EMBEDDING_BASE_URL}
EMBEDDING_MODEL={EMBEDDING_MODEL}
EOF
```

**Windows:**
```
if not exist "%USERPROFILE%\.terrain" mkdir "%USERPROFILE%\.terrain"
(
echo LLM_API_KEY={LLM_API_KEY}
echo LLM_BASE_URL={LLM_BASE_URL}
echo LLM_MODEL={LLM_MODEL}
echo EMBEDDING_API_KEY={EMBEDDING_API_KEY}
echo EMBEDDING_BASE_URL={EMBEDDING_BASE_URL}
echo EMBEDDING_MODEL={EMBEDDING_MODEL}
) > "%USERPROFILE%\.terrain\.env"
```

Omit any line whose value was not provided.

---

## Block 5 — Register terrain as an MCP Server

Detect which AI agent client the user is running and register the MCP server accordingly.

### Claude Code

**Mac/Linux:**
```bash
claude mcp add terrain -- npx terrain-ai@latest --server
```

**Windows:**
```
claude mcp add terrain -- cmd /c npx terrain-ai@latest --server
```

Verify registration:
```bash
claude mcp list
```
Expected: `terrain` appears in the list.

---

### opencode

Read the current config file:

**Mac/Linux:** `~/.config/opencode/opencode.json` (or `$XDG_CONFIG_HOME/opencode/opencode.json` if set)
**Windows:** `%APPDATA%\opencode\opencode.json`

Add the following entry inside the `"mcpServers"` object (create the key if absent):

**Mac/Linux:**
```json
"terrain": {
  "command": "npx",
  "args": ["-y", "terrain-ai@latest", "--server"]
}
```

**Windows:**
```json
"terrain": {
  "command": "cmd",
  "args": ["/c", "npx", "-y", "terrain-ai@latest", "--server"]
}
```

---

### Other agents (codex, Hermes, etc.)

Tell the user: "Please add the following to your agent's MCP configuration file:"

**Mac/Linux:**
```json
{
  "mcpServers": {
    "terrain": {
      "command": "npx",
      "args": ["-y", "terrain-ai@latest", "--server"]
    }
  }
}
```

**Windows:**
```json
{
  "mcpServers": {
    "terrain": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "terrain-ai@latest", "--server"]
    }
  }
}
```

If the MCP registration command fails, show the appropriate JSON block above and instruct the user to add it manually.

---

## Block 6 — Install Slash Command (Self-Perpetuation)

Write a local slash command file so the user can trigger installation in future sessions by saying "install terrain" — without needing the bootstrap URL again.

**Claude Code — Mac/Linux:**
```bash
mkdir -p ~/.claude/commands
cat > ~/.claude/commands/terrain.md << 'EOF'
Install terrain-ai by following the instructions at:
https://raw.githubusercontent.com/JeremyJiao01/Terrain-AI/main/AGENT_INSTALL.md
EOF
```

**Claude Code — Windows:**
```
if not exist "%USERPROFILE%\.claude\commands" mkdir "%USERPROFILE%\.claude\commands"
echo Install terrain-ai by following the instructions at: > "%USERPROFILE%\.claude\commands\terrain.md"
echo https://raw.githubusercontent.com/JeremyJiao01/Terrain-AI/main/AGENT_INSTALL.md >> "%USERPROFILE%\.claude\commands\terrain.md"
```

**opencode — Mac/Linux:**
```bash
mkdir -p ~/.config/opencode/command
cat > ~/.config/opencode/command/terrain.md << 'EOF'
Install terrain-ai by following the instructions at:
https://raw.githubusercontent.com/JeremyJiao01/Terrain-AI/main/AGENT_INSTALL.md
EOF
```

**opencode — Windows:**
```
if not exist "%APPDATA%\opencode\command" mkdir "%APPDATA%\opencode\command"
echo Install terrain-ai by following the instructions at: > "%APPDATA%\opencode\command\terrain.md"
echo https://raw.githubusercontent.com/JeremyJiao01/Terrain-AI/main/AGENT_INSTALL.md >> "%APPDATA%\opencode\command\terrain.md"
```

---

## Block 7 — Validate Installation

Run both commands. Do not declare success until both pass.

**Mac/Linux:**
```bash
terrain --version
terrain status
```

**Windows:**
```
terrain --version
terrain status
```

**Expected:**
- `terrain --version` prints a version string (e.g. `terrain 2.1.14`)
- `terrain status` prints workspace and service status without errors

**If either command fails:**
- `terrain --version` fails → the pip install (Block 3) did not complete correctly. Re-run Block 3.
- `terrain status` fails → the MCP registration (Block 5) or `.env` configuration (Block 4.5) may be incomplete. Report the full error output.

Do not proceed past this block until both commands succeed.

---

## Installation Complete

terrain-ai is now installed and configured. Tell the user:

---

**terrain-ai is ready. Here's what to do next:**

**Step 1 — Index your codebase**

Point terrain at the repo you want to explore:

```bash
terrain index /path/to/your/repo
```

This runs once and takes a few minutes. Incremental updates after that are fast:

```bash
terrain index -i
```

**Step 2 — Ask questions about the code**

In this agent session, you can now ask things like:

- "How does authentication work in this codebase?"
- "Find the function that handles payment processing"
- "Trace the call chain for the login flow"
- "What calls the `refresh_token` function?"

terrain will search the knowledge graph and return precise results with signatures, call trees, and source locations.

**Step 3 — Switch between repos**

If you work on multiple codebases, index each one and switch between them:

```bash
terrain list        # show all indexed repos
terrain repo        # interactively switch active repo
```

**Re-run setup anytime** by saying **"install terrain"** in any agent session.
