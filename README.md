# Code Graph Builder

English | [Chinese / CN](README_CN.md)

Build a knowledge graph from any codebase, generate API documentation, and search code semantically -- all accessible as an MCP server for AI coding assistants.

## What It Does

```
Your Code Repository
    |
    v
[Tree-sitter AST Parsing]  -->  Knowledge Graph (Kuzu)
    |                               |
    |                               v
    |                        API Documentation (Markdown)
    |                               |
    |                               v
    |                        Vector Embeddings
    |                               |
    v                               v
MCP Server  <--------------  Semantic Search
    |
    v
Claude Code / Cursor / Windsurf / Any MCP Client
```

**Core workflow for AI agents:**

```
initialize_repository  ->  find_api  ->  get_api_doc
```

1. Index the codebase once
2. Search by vague semantic description ("PWM duty cycle update")
3. Get precise function signatures, call trees, and usage examples

## Quick Start

### Install via npx (recommended)

```bash
# First run --interactive setup wizard
npx code-graph-builder@latest --setup

# Start MCP server
npx code-graph-builder@latest --server
```

The setup wizard:
1. Auto-installs the Python package if not found
2. Configures workspace, LLM provider, and embedding provider
3. Runs an MCP smoke test to verify the server works
4. Optionally registers as a global MCP server for Claude Code (`claude mcp add --scope user`)

### Install via pip

```bash
pip install code-graph-builder
cgb-mcp  # Start MCP server
```

### Uninstall

```bash
npx code-graph-builder@latest --uninstall
```

Removes: Claude MCP registration, Python package, workspace data.

### MCP Client Configuration

Add to your MCP client config (Claude Code, Cursor, Windsurf, etc.):

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "npx",
      "args": ["-y", "code-graph-builder@latest", "--server"]
    }
  }
}
```

On Windows, use:

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "code-graph-builder@latest", "--server"]
    }
  }
}
```

## Architecture

The project follows a 5-layer harness architecture:

```
L4  entrypoints/         MCP server, CLI
L3  domains/upper/       apidoc, rag, guidance, calltrace
L2  domains/core/        graph, embedding, search
L1  foundation/          parsers, services, utils
L0  foundation/types/    constants, models, type definitions
```

## Pipeline

| Step | What | Input | Output |
|------|------|-------|--------|
| 1. graph-build | Tree-sitter AST parsing | Source code | Kuzu graph database |
| 2. api-doc-gen | Query graph, render docs | Graph | 3-level Markdown (index / module / function) |
| 2b. desc-gen | LLM generates descriptions | Functions without docstrings | Descriptions in L3 Markdown |
| 3. embed-gen | Vectorize function docs | L3 Markdown files | Vector store (pickle) |

Steps 1-3 run automatically via `initialize_repository`. Wiki generation is available separately via `generate_wiki`.

```
initialize_repository  ->  Steps 1-3 (full pipeline)
build_graph            ->  Step 1 only
generate_api_docs      ->  Step 2 + 2b (modes: full / resume / enhance)
rebuild_embeddings     ->  Step 3
generate_wiki          ->  Separate (not in main pipeline)
```

### API Doc Generation Modes

| Mode | Behavior |
|------|----------|
| `full` | Rebuild all docs from graph |
| `resume` | Generate only for functions with TODO placeholders |
| `enhance` | LLM-powered module summaries + API usage workflows |

## MCP Tools

### Primary Tools (13 exposed)

#### Repository Management
| Tool | Description |
|------|-------------|
| `initialize_repository` | Index a repo: graph + API docs + embeddings |
| `get_repository_info` | Active repo stats (node/relationship counts, service status) |
| `list_repositories` | All indexed repos with pipeline completion status |
| `switch_repository` | Switch active repo for queries |
| `link_repository` | Reuse existing index for a different repo path (no re-indexing) |

#### Code Search & Documentation
| Tool | Description |
|------|-------------|
| `find_api` | Hybrid semantic + keyword search with API doc (primary search tool) |
| `list_api_docs` | Browse L1 module index or L2 module details |
| `get_api_doc` | L3 function detail: signature, call tree, usage examples, source |
| `generate_api_docs` | Generate/update API docs (full / resume / enhance) |

#### Call Graph Analysis
| Tool | Description |
|------|-------------|
| `find_callers` | Find all functions that call a specific function (no LLM required) |
| `trace_call_chain` | BFS upward call chain trace with entry point discovery |

#### Configuration & Maintenance
| Tool | Description |
|------|-------------|
| `get_config` | Show server configuration and service availability |
| `rebuild_embeddings` | Build or rebuild vector embeddings |

### Hidden Tools (available via handler)

These tools are superseded by the API-doc-based workflow above but remain accessible:
`query_code_graph`, `get_code_snippet`, `semantic_search`, `locate_function`, `list_api_interfaces`, `list_wiki_pages`, `get_wiki_page`, `generate_wiki`, `build_graph`, `prepare_guidance`

## API Documentation Format

Generated docs are optimized for both AI agent reading and vector retrieval.

### L3 Function Detail (embedding unit)

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

## Parameters & Memory

| Parameter | Direction | Ownership |
|-----------|-----------|-----------|
| `CType *type` | in/out | borrowed, modified |
| `AttributeDef *ad` | in/out | borrowed, modified |

## Implementation

```c
int parse_btype(CType *type, AttributeDef *ad, int ignore_label) {
    // ... source code embedded
}
```
```

### C/C++ Specific Features

- Extracts `//` and `/* */` comments above functions as descriptions
- Struct/union/enum members displayed with types
- Macro definitions in dedicated section
- Static/public/extern visibility classification
- Memory ownership inference from signatures
- Header/implementation file split
- Cross-file function call resolution via `#include` header mapping
- Function pointer tracking and indirect call resolution
- GB2312/GBK encoding support for source files

## Supported Languages

| Language | Functions | Classes/Structs | Calls | Imports | Types |
|----------|-----------|-----------------|-------|---------|-------|
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

## Graph Schema

**Nodes**: `Project`, `Package`, `Module`, `File`, `Folder`, `Class`, `Function`, `Method`, `Type`, `Enum`, `Union`

**Relationships**: `CONTAINS_*`, `DEFINES`, `DEFINES_METHOD`, `CALLS`, `INHERITS`, `IMPLEMENTS`, `IMPORTS`, `OVERRIDES`

**Properties**: `qualified_name` (PK), `name`, `path`, `start_line`, `end_line`, `signature`, `return_type`, `visibility`, `parameters`, `kind`, `docstring`

## Environment Variables

### LLM (first match wins)

| Variable | Purpose | Default |
|----------|---------|---------|
| `LLM_API_KEY` | Generic LLM key (highest priority) | - |
| `LLM_BASE_URL` | API endpoint | `https://api.openai.com/v1` |
| `LLM_MODEL` | Model name | `gpt-4o` |
| `OPENAI_API_KEY` | OpenAI or compatible | - |
| `MOONSHOT_API_KEY` | Moonshot / Kimi (legacy) | - |

### Embedding

| Variable | Purpose | Default |
|----------|---------|---------|
| `DASHSCOPE_API_KEY` | DashScope (Qwen3 Embedding) | - |
| `DASHSCOPE_BASE_URL` | DashScope endpoint | `https://dashscope.aliyuncs.com/api/v1` |

### System

| Variable | Purpose | Default |
|----------|---------|---------|
| `CGB_WORKSPACE` | Workspace directory | `~/.code-graph-builder` |

## Installation Options

### Install from PyPI

```bash
# Core (includes C/C++, Python, JS/TS grammars)
pip install code-graph-builder

# With all language grammars (Rust, Go, Java, Scala, Lua)
pip install "code-graph-builder[treesitter-full]"
```

### Install from local source

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

# Install with all language grammars
pip install ".[treesitter-full]"

# Or install in editable mode for development
pip install -e ".[treesitter-full]"
```

### Build and install from wheel

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

# Build wheel and sdist
python3 -m build

# Install the wheel
pip install dist/code_graph_builder-*.whl

# Or force reinstall over existing version
pip install --force-reinstall dist/code_graph_builder-*.whl
```

## Development

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki
pip install -e ".[treesitter-full]"

# Run tests
python3 -m pytest code_graph_builder/tests/ -v

# Integration tests (requires tinycc repo at ../tinycc)
python3 -m pytest code_graph_builder/tests/domains/core/test_graph_build.py -v      # ~3 min
python3 -m pytest code_graph_builder/tests/domains/upper/test_api_docs.py -v        # ~3 min
python3 -m pytest code_graph_builder/tests/domains/core/test_step3_embedding.py -v  # ~27 min (API calls)
python3 -m pytest code_graph_builder/tests/domains/upper/test_api_find_integration.py -v  # ~47 min (full pipeline)
```

## License

MIT
