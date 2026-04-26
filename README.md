# Terrain

English | [Chinese / CN](README_CN.md)

[![CI](https://github.com/JeremyJiao01/Terrain-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/JeremyJiao01/Terrain-AI/actions)
[![PyPI](https://img.shields.io/pypi/v/terrain-ai)](https://pypi.org/project/terrain-ai/)
[![npm](https://img.shields.io/npm/v/terrain-ai)](https://www.npmjs.com/package/terrain-ai)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org)
[![Node](https://img.shields.io/badge/node-18+-green)](https://nodejs.org)

Give your AI coding assistant a complete map of any codebase — function signatures, call graphs, and semantic search across every line of code.

## The Problem

You drop a 500,000-line codebase in front of Claude Code. It reads what it can see. It guesses what it can't. You get answers that are *almost* right.

Terrain indexes the entire codebase once, then gives your AI a precise, queryable knowledge graph — so it stops guessing.

## What This Looks Like

Ask Claude Code about an unfamiliar codebase:

> "How does the authentication token get refreshed?"

Without Terrain, the AI skims files and makes educated guesses — possibly missing the real implementation buried three call levels deep.

With Terrain:

```
find_api("authentication token refresh")

→ refresh_access_token() in auth/token_manager.c:187
  Signature: int refresh_access_token(TokenCtx *ctx, const char *refresh_token)
  Called by: session_heartbeat() → event_loop_tick() → main()
  Calls:     http_post(), parse_jwt(), update_session_store()
```

Precise. Complete. Instant.

---


## Full Installation

### Install the npm package

The npm package provides the CLI wrapper and MCP server launcher:

```bash
npm install -g terrain-ai@latest
```

### Install the Python package (PyPI)

The Python package provides the core indexing engine, graph database, and all language parsers.

**Core installation** (includes C/C++, Python, JavaScript/TypeScript grammars):

```bash
pip install terrain-ai
```

## Quick Start — Agent Install (Recommended)

Already using an AI agent like Claude Code, opencode, or codex? Paste this into your agent chat:

```
Please follow the installation instructions at:
https://raw.githubusercontent.com/JeremyJiao01/Terrain-AI/main/AGENT_INSTALL.md
```

Your agent will handle everything: Python 3.11 check, package install, API key setup, and MCP registration. After the first run, you can re-trigger by saying **"install terrain"** in any session.

---

### Manual Install (Alternative)

```bash
npx terrain-ai@latest --setup
```

The setup wizard installs the Python package, configures your LLM and embedding provider, and registers Terrain as a global MCP server for supported clients. One command.

**Supported clients (auto-detected):**
- **Claude Code** — MCP registered via `claude mcp add`; slash commands installed to `~/.claude/commands/`
- **opencode** — MCP registered by editing `~/.config/opencode/opencode.json` (respects `$XDG_CONFIG_HOME`); slash commands installed to `~/.config/opencode/command/`

## Index a Codebase

```bash
terrain index /path/to/your/repo
```

Takes a few minutes the first time. Incremental updates after that:

```bash
terrain index -i   # git-diff based, fast
```

## What You Can Ask

| You want to know... | What to ask |
|---|---|
| Where does X get initialized? | "find where X is initialized" |
| What calls this function? | "find callers of function\_name" |
| How does feature Y work end-to-end? | "trace the call chain for Y" |
| What functions handle Z? | "find Z handler" |

## Supported Languages

C/C++, Python, JavaScript/TypeScript, Rust, Go, Java, Scala, C#, PHP, Lua

---

## Reference

### Uninstall

```bash
npx terrain-ai@latest --uninstall
```

Removes: Claude MCP registration, opencode MCP registration, slash commands from both clients, Python package, workspace data.

### CLI Tool (`terrain`)

#### Workspace

```bash
terrain status              # Show active repository, workspace, LLM & embedding info
terrain list                # List all indexed repositories
terrain repo                # Interactively switch active repository
terrain config              # Interactive configuration wizard (LLM, embedding, workspace)
terrain link <path>         # Link a local repo to shared pre-built artifacts
terrain link <path> --db x  # Link to a specific artifact directory
```

#### Indexing

```bash
terrain index               # Index current directory (graph → api-docs → embeddings)
terrain index /path/to/repo # Index a specific path
terrain index -i            # Incremental update (git-diff based, fast)
terrain index --no-embed    # Skip embedding generation
terrain index --no-wiki     # Skip wiki generation only
```

#### Rebuild & Clean

```bash
terrain rebuild             # Rebuild all steps for active repository
terrain rebuild --step graph   # Rebuild only the graph
terrain rebuild --step api     # Rebuild only API docs
terrain rebuild --step embed   # Rebuild only embeddings
terrain rebuild --step wiki    # Rebuild only wiki

terrain clean               # Remove indexed data (interactive)
terrain clean repo_name     # Remove specific repository
terrain clean --all         # Remove all indexed repositories
```

#### Low-Level Commands

```bash
terrain scan /path          # Scan repo and build knowledge graph
  --backend kuzu|memgraph|memory
  --db-path ./graph.db
  --exclude "vendor,build"
  --language "c,python"
  --clean               # Clean DB before scanning
  -o graph.json         # Export graph to JSON

terrain query "MATCH (f:Function) RETURN f.name LIMIT 10"
  --format table|json

terrain export /path -o graph.json
  --build               # Build graph before exporting

terrain stats               # Show graph statistics (nodes, relationships)
```

#### Global Flags

```bash
terrain --version           # Show version
terrain -v ...              # Verbose/debug output
terrain --help              # Show help
```

### MCP Tools

**Core workflow for AI agents:** `initialize_repository` → `find_api` → `get_api_doc`

#### Repository Management

| Tool | Description |
|---|---|
| `initialize_repository` | Index a repo: graph + API docs + embeddings |
| `get_repository_info` | Active repo stats (node/relationship counts, service status) |
| `list_repositories` | All indexed repos with pipeline completion status |
| `switch_repository` | Switch active repo for queries |
| `link_repository` | Reuse existing index for a different repo path (no re-indexing) |

#### Code Search & Documentation

| Tool | Description |
|---|---|
| `find_api` | Hybrid semantic + keyword search with API doc (primary search tool) |
| `list_api_docs` | Browse L1 module index or L2 module details |
| `get_api_doc` | L3 function detail: signature, call tree, usage examples, source |
| `generate_api_docs` | Generate/update API docs (full / resume / enhance) |

#### Call Graph Analysis

| Tool | Description |
|---|---|
| `find_callers` | Find all functions that call a specific function (no LLM required) |
| `trace_call_chain` | BFS upward call chain trace with entry point discovery |

#### Configuration & Maintenance

| Tool | Description |
|---|---|
| `rebuild_embeddings` | Build or rebuild vector embeddings |

### Pipeline

| Step | What | Input | Output |
|---|---|---|---|
| 1. graph-build | Tree-sitter AST parsing | Source code | Kuzu graph database |
| 2. api-doc-gen | Query graph, render docs | Graph | 3-level Markdown (index / module / function) |
| 2b. desc-gen | LLM generates descriptions | Functions without docstrings | Descriptions in L3 Markdown |
| 3. embed-gen | Vectorize function docs | L3 Markdown files | Vector store (pickle) |

```
initialize_repository  ->  Steps 1-3 (full pipeline)
build_graph            ->  Step 1 only
generate_api_docs      ->  Step 2 + 2b (modes: full / resume / enhance)
rebuild_embeddings     ->  Step 3
generate_wiki          ->  Separate (not in main pipeline)
```

### API Documentation Format

Generated docs are optimized for both AI agent reading and vector retrieval.

#### L3 Function Detail (embedding unit)

```markdown
# parse_btype

> Parse base type declaration including struct/union/enum specifiers.

- Signature: `int parse_btype(CType *type, AttributeDef *ad, int ignore_label)`
- Return: `int`
- Visibility: static | Header: tccgen.h
- Location: tccgen.c:139-280
- Module: tinycc.tccgen --C code generator

## Call Tree

parse_btype
|-- expr_const           [static]
|-- parse_btype_qualify   [static]
|-- struct_decl           [static]
|   |-- expect
|   `-- next
`-- parse_attribute       [static]

## Called by (5)

- type_decl (tinycc.tccgen) -> tccgen.c:1200
- post_type (tinycc.tccgen) -> tccgen.c:1350
```

#### C/C++ Specific Features

- Extracts `//` and `/* */` comments above functions as descriptions
- Struct/union/enum members displayed with types
- Macro definitions in dedicated section
- Static/public/extern visibility classification
- Memory ownership inference from signatures
- Header/implementation file split
- Cross-file function call resolution via `#include` header mapping
- Function pointer tracking and indirect call resolution
- GB2312/GBK encoding support for source files

### Supported Languages (detail)

| Language | Functions | Classes/Structs | Calls | Imports | Types |
|---|---|---|---|---|---|
| C / C++ | Yes | struct, union, enum, typedef, macro | Yes | #include | Yes |
| Python | Yes | Yes | Yes | Yes | - |
| JavaScript / TypeScript | Yes | Yes | Yes | Yes | - |
| Rust | Yes | struct, enum, trait, impl | Yes | Yes | - |
| Go | Yes | struct, interface | Yes | Yes | - |
| Java | Yes | class, interface, enum | Yes | Yes | - |
| Scala | Yes | class, object | Yes | Yes | - |
| C# | Yes | class, namespace | Yes | - | - |
| PHP | Yes | class | Yes | - | - |
| Lua | Yes | - | Yes | - | - |

### Graph Schema

**Nodes**: `Project`, `Package`, `Module`, `File`, `Folder`, `Class`, `Function`, `Method`, `Type`, `Enum`, `Union`

**Relationships**: `CONTAINS_*`, `DEFINES`, `DEFINES_METHOD`, `CALLS`, `INHERITS`, `IMPLEMENTS`, `IMPORTS`, `OVERRIDES`

**Properties**: `qualified_name` (PK), `name`, `path`, `start_line`, `end_line`, `signature`, `return_type`, `visibility`, `parameters`, `kind`, `docstring`

### Architecture

The project follows a 5-layer harness architecture:

```
L4  entrypoints/         MCP server, CLI
L3  domains/upper/       apidoc, rag, guidance, calltrace
L2  domains/core/        graph, embedding, search
L1  foundation/          parsers, services, utils
L0  foundation/types/    constants, models, type definitions
```

### Environment Variables

#### LLM (first match wins)

| Variable | Purpose | Default |
|---|---|---|
| `LLM_API_KEY` | Generic LLM key (highest priority) | - |
| `LLM_BASE_URL` | API endpoint | `https://api.openai.com/v1` |
| `LLM_MODEL` | Model name | `gpt-4o` |
| `OPENAI_API_KEY` | OpenAI or compatible | - |
| `MOONSHOT_API_KEY` | Moonshot / Kimi (legacy) | - |

#### Embedding

| Variable | Purpose | Default |
|---|---|---|
| `DASHSCOPE_API_KEY` | DashScope (Qwen3 Embedding) | - |
| `DASHSCOPE_BASE_URL` | DashScope endpoint | `https://dashscope.aliyuncs.com/api/v1` |

#### System

| Variable | Purpose | Default |
|---|---|---|
| `TERRAIN_WORKSPACE` | Workspace directory | `~/.terrain` |

### Installation Options

#### Install from PyPI

```bash
# Core (includes C/C++, Python, JS/TS grammars)
pip install terrain-ai

# With all language grammars (Rust, Go, Java, Scala, Lua)
pip install "terrain-ai[treesitter-full]"
```

#### Install from npm

```bash
# Global install (recommended for CLI usage)
npm install -g terrain-ai@latest

# Or run directly with npx (no install needed)
npx terrain-ai@latest --version
```

#### Install from local source

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

# Install with all language grammars
pip install ".[treesitter-full]"

# Or install in editable mode for development
pip install -e ".[treesitter-full]"
```

#### Build and install from wheel

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

python3 -m build
pip install dist/terrain_ai-*.whl
```

### Development

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki
pip install -e ".[treesitter-full]"

python3 -m pytest tests/ -v

# Integration tests (requires tinycc repo at ../tinycc)
python3 -m pytest tests/domains/core/test_graph_build.py -v      # ~3 min
python3 -m pytest tests/domains/upper/test_api_docs.py -v        # ~3 min
python3 -m pytest tests/domains/core/test_step3_embedding.py -v  # ~27 min (API calls)
python3 -m pytest tests/domains/upper/test_api_find_integration.py -v  # ~47 min (full pipeline)
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
