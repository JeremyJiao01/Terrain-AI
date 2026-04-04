# Architecture

## Layer Model

```
L0  foundation/types/                          Pure data: constants, types, config, models
L1  foundation/{parsers,services,utils}/       Shared infra: AST parsing, DB drivers, utilities
L2  domains/core/{graph,embedding,search}/     Core domains: graph build, embeddings, search
L3  domains/upper/{apidoc,rag,guidance}/       Upper domains: API docs, RAG, guidance agents
L4  entrypoints/{mcp,cli}/                     Entry points: MCP server, CLI commands
```

### Mapping to Source Tree

| Layer | Source path(s) |
|-------|---------------|
| L0 | `code_graph_builder/constants.py`, `code_graph_builder/types.py`, `code_graph_builder/config.py`, `code_graph_builder/models.py`, `code_graph_builder/settings.py` |
| L1 | `code_graph_builder/parsers/`, `code_graph_builder/services/`, `code_graph_builder/utils/`, `code_graph_builder/language_spec.py`, `code_graph_builder/parser_loader.py` |
| L2 | `code_graph_builder/builder.py`, `code_graph_builder/graph_updater.py`, `code_graph_builder/embeddings/` |
| L3 | `code_graph_builder/mcp/api_doc_generator.py`, `code_graph_builder/rag/`, `code_graph_builder/guidance/` |
| L4 | `code_graph_builder/mcp/server.py`, `code_graph_builder/mcp/tools.py`, `code_graph_builder/mcp/pipeline.py`, `code_graph_builder/cli.py`, `code_graph_builder/cgb_cli.py`, `code_graph_builder/commands_cli.py` |

## Dependency Rules

| Layer | May import | Must NOT import |
|-------|-----------|-----------------|
| L0 | stdlib, third-party | Any project module |
| L1 | L0 | L2, L3, L4 |
| L2 | L0, L1 | L3, L4, other L2 domains |
| L3 | L0, L1, L2 | L4, other L3 domains |
| L4 | L0, L1, L2, L3 | Other L4 entrypoints |

**One-line rule:** Upper layers import lower layers. Never reverse. Never cross-domain at same layer.

## Enforcement

```bash
python tools/dep_check.py
```

Run before every commit. CI will reject violations.
