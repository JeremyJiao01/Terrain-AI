Build the code knowledge graph from source code using Tree-sitter AST parsing. This is step 1 of the pipeline — it only creates the graph database, without generating API docs, embeddings, or wiki.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py graph-build $ARGUMENTS
```

Supported options:
- `--rebuild` : Force rebuild graph even if cached
- `--backend kuzu|memgraph|memory` : Graph database backend (default: kuzu)

Examples:
- `/graph-build /home/user/my-project` — build graph
- `/graph-build /home/user/my-project --rebuild` — force full rebuild

After building, use `/api-doc-gen`, `/embed-gen`, and `/wiki-gen` as separate steps.
