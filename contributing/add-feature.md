# Add a Feature

File checklists per scenario. Follow the layer rules in `contributing/architecture.md`.

---

## Adding a New Parser (L1)

1. `code_graph_builder/constants.py` -- add language to `SupportedLanguage` enum.
2. `code_graph_builder/language_spec.py` -- add `_<lang>_get_name`, `_<lang>_file_to_module`, and a `LanguageSpec` entry.
3. `code_graph_builder/parsers/factory.py` -- register the new language in `ProcessorFactory`.
4. `code_graph_builder/parser_loader.py` -- add grammar loading logic.
5. `pyproject.toml` -- add `tree-sitter-<lang>` dependency (core or `treesitter-full`).
6. `code_graph_builder/tests/test_<lang>.py` -- unit + integration tests.

**Layer rule:** Parsers (L1) may only import from L0. Do not import builder, embeddings, or MCP modules.

---

## Adding a New Database Backend (L1 + L2)

1. `code_graph_builder/services/<backend>_service.py` -- implement service matching `IngestorProtocol` (L1).
2. `code_graph_builder/services/__init__.py` -- export the new service.
3. `code_graph_builder/builder.py` -- wire the new backend as an option (L2).
4. `code_graph_builder/config.py` -- add config keys if needed (L0).
5. `code_graph_builder/tests/test_<backend>_service.py` -- tests.

**Layer rule:** Service module (L1) imports L0 only. Builder (L2) imports L0 + L1.

---

## Adding a New Embedding Model (L2)

1. `code_graph_builder/embeddings/<model>_embedder.py` -- implement embedder.
2. `code_graph_builder/embeddings/__init__.py` -- export.
3. `code_graph_builder/config.py` -- add model selection config (L0).
4. `code_graph_builder/tests/test_embedder.py` -- add test cases.

**Layer rule:** Embeddings (L2) may import L0 and L1. Do not import rag, guidance, or MCP.

---

## Adding a New MCP Tool (L4)

1. `code_graph_builder/mcp/tools.py` -- add tool function.
2. `code_graph_builder/mcp/server.py` -- register the tool.
3. `code_graph_builder/tests/test_mcp_protocol.py` -- protocol-level test.
4. `code_graph_builder/tests/test_mcp_user_flow.py` -- user-flow test.

**Layer rule:** MCP (L4) may import any lower layer. Do not import CLI modules.

---

## Adding a New CLI Command (L4)

1. `code_graph_builder/commands_cli.py` -- add command function.
2. `code_graph_builder/cli.py` or `code_graph_builder/cgb_cli.py` -- register the command.
3. `code_graph_builder/tests/test_cli_<command>.py` -- tests.

**Layer rule:** CLI (L4) may import any lower layer. Do not import MCP modules.

---

## Adding a RAG Feature (L3)

1. `code_graph_builder/rag/<feature>.py` -- implement feature.
2. `code_graph_builder/rag/__init__.py` -- export.
3. `code_graph_builder/rag/config.py` -- add config if needed.
4. `code_graph_builder/tests/test_rag.py` -- add test cases.

**Layer rule:** RAG (L3) may import L0, L1, L2. Do not import guidance, MCP, or CLI.
