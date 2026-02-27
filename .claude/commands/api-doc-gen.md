Generate hierarchical API documentation from the existing knowledge graph. This is step 2 of the pipeline. Produces L1 module index, L2 per-module pages, and L3 per-function detail pages with call graphs.

Requires only a graph database — no embeddings or LLM API keys needed.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py api-doc-gen $ARGUMENTS
```

Supported options:
- `--rebuild` : Force regenerate API docs even if cached

Examples:
- `/api-doc-gen` — generate API docs (uses cache if available)
- `/api-doc-gen --rebuild` — force full regeneration

Requires `/graph-build` or `/repo-init` to have been run at least once (graph must exist).
