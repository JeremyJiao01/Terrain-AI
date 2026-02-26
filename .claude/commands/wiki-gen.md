Regenerate wiki pages using existing graph and embeddings. Use this when wiki generation failed during `/repo-init`, or when you want to regenerate with different settings — without rebuilding the graph or recomputing embeddings.

```bash
python3 -m code_graph_builder.commands_cli wiki-gen $ARGUMENTS
```

Supported options:
- `--rebuild` : Force regenerate wiki structure and all pages (ignores cache)
- `--mode comprehensive|concise` : comprehensive = 8-10 pages (default), concise = 4-5 pages

Examples:
- `/wiki-gen` — regenerate with default settings
- `/wiki-gen --rebuild` — force full regeneration
- `/wiki-gen --mode concise` — generate fewer pages

Requires `/repo-init` to have been run at least once (graph + embeddings must exist).
