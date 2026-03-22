# Code Graph Builder

Build a knowledge graph from any codebase, generate API documentation, and search code semantically — all accessible as an MCP server for AI coding assistants.

## What It Does

```
Your Code Repository
    |
    v
[Tree-sitter AST Parsing]  ──>  Knowledge Graph (Kuzu)
    |                               |
    |                               v
    |                        API Documentation (Markdown)
    |                               |
    |                               v
    |                        Vector Embeddings
    |                               |
    v                               v
MCP Server  <──────────────  Semantic Search
    |
    v
Claude Code / OpenCode / Cursor / Any MCP Client
```

## Quick Start

### Install via npx (recommended)

```bash
# First run — interactive setup wizard
npx code-graph-builder

# Subsequent runs — MCP clients use this
npx code-graph-builder --server
```

The setup wizard guides you through:
1. Workspace directory
2. LLM provider (Moonshot / OpenAI / DeepSeek / OpenRouter / Custom)
3. Embedding provider (DashScope / OpenAI / Custom)

### Install via pip

```bash
pip install "code-graph-builder[treesitter-c,semantic]"
cgb-mcp  # Start MCP server
```

### MCP Client Configuration

Add to your MCP client config (Claude Code, OpenCode, Cursor, etc.):

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "npx",
      "args": ["-y", "code-graph-builder", "--server"]
    }
  }
}
```

## Pipeline

| Step | What | Input | Output |
|------|------|-------|--------|
| 1. graph-build | Tree-sitter AST parsing | Source code | Kuzu graph database |
| 2. api-doc-gen | Query graph, render docs | Graph | 3-level Markdown (index / module / function) |
| 2b. desc-gen | LLM generates descriptions | Functions without comments | `> description` in Markdown |
| 3. embed-gen | Vectorize function docs | L3 Markdown | Vector store (pickle) |
| 4. wiki-gen | LLM generates wiki pages | Embeddings + graph | Multi-page wiki |

All steps run automatically via `initialize_repository`, or individually:

```
initialize_repository  →  Steps 1-4 (full pipeline)
build_graph            →  Step 1 only
generate_api_docs      →  Step 2 + 2b
rebuild_embeddings     →  Step 3
generate_wiki          →  Step 4
```

## MCP Tools (19 tools)

### Repository Management
| Tool | Description |
|------|-------------|
| `initialize_repository` | Index a repo: graph + API docs + embeddings + wiki |
| `get_repository_info` | Active repo metadata and graph statistics |
| `list_repositories` | All indexed repos in workspace |
| `switch_repository` | Switch active repo |

### Code Search & Navigation
| Tool | Description |
|------|-------------|
| `find_api` | Semantic search + API doc attachment (primary search tool) |
| `semantic_search` | Vector similarity search across codebase |
| `query_code_graph` | Natural language → Cypher → graph query |
| `get_code_snippet` | Retrieve source code by qualified name |
| `locate_function` | Find function in file using Tree-sitter |

### API Documentation
| Tool | Description |
|------|-------------|
| `list_api_docs` | Browse L1 index or L2 module details |
| `get_api_doc` | L3 function detail (signature, call tree, source) |
| `list_api_interfaces` | List public APIs by module/visibility |
| `generate_api_docs` | Regenerate API documentation |

### Wiki & Analysis
| Tool | Description |
|------|-------------|
| `list_wiki_pages` | List generated wiki pages |
| `get_wiki_page` | Read wiki page content |
| `generate_wiki` | Regenerate wiki pages |
| `rebuild_embeddings` | Rebuild vector embeddings |
| `build_graph` | Build/rebuild knowledge graph |
| `prepare_guidance` | Analyze design doc, generate code guidance |

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
- Module: tinycc.tccgen — C code generator

## Call Tree

parse_btype
├── expr_const           [static]
├── parse_btype_qualify   [static]
├── struct_decl           [static]
│   ├── expect
│   └── next
└── parse_attribute       [static]

## Called by (5)

- type_decl (tinycc.tccgen) → tccgen.c:1200
- post_type (tinycc.tccgen) → tccgen.c:1350

## Parameters & Memory

| Parameter | Direction | Ownership |
|-----------|-----------|-----------|
| `CType *type` | in/out | borrowed, modified |
| `AttributeDef *ad` | in/out | borrowed, modified |

## Implementation

​```c
int parse_btype(CType *type, AttributeDef *ad, int ignore_label) {
    // ... source code embedded
}
​```
```

### C/C++ Specific Features

- Extracts `//` and `/* */` comments above functions as descriptions
- Struct/union/enum members displayed with types
- Macro definitions in dedicated section
- Static/public/extern visibility classification
- Memory ownership inference from signatures
- Header/implementation file split

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

```bash
# Core only (graph building)
pip install code-graph-builder

# With C/C++ support
pip install "code-graph-builder[treesitter-c]"

# With all languages
pip install "code-graph-builder[treesitter-full]"

# With semantic search
pip install "code-graph-builder[semantic]"

# Everything
pip install "code-graph-builder[treesitter-full,semantic,rag]"
```

## Development

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki
pip install -e ".[treesitter-full,semantic,rag]"

# Run tests
python3 -m pytest code_graph_builder/tests/ -v

# Integration tests (requires tinycc repo at ../tinycc)
python3 -m pytest code_graph_builder/tests/test_step1_graph_build.py -v   # ~3 min
python3 -m pytest code_graph_builder/tests/test_step2_api_docs.py -v      # ~3 min
python3 -m pytest code_graph_builder/tests/test_step3_embedding.py -v     # ~27 min (API calls)
python3 -m pytest code_graph_builder/tests/test_api_find_integration.py -v # ~47 min (full pipeline)
```

## License

MIT
