Rebuild vector embeddings using the existing knowledge graph. Use this when embeddings are missing, corrupted, or when you want to re-embed after changing the embedding model or configuration — without rebuilding the graph.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py embed-gen $ARGUMENTS
```

Supported options:
- `--rebuild` : Force rebuild embeddings even if cached

Examples:
- `/embed-gen` — rebuild embeddings (uses cache if available)
- `/embed-gen --rebuild` — force full re-embedding

Requires `/repo-init` to have been run at least once (graph must exist).
