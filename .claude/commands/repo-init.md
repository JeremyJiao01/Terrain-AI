Index a code repository by running the full pipeline: graph-build → api-doc-gen → embed-gen → wiki-gen.

This is the all-in-one command. Each step can also be run individually:
- `/graph-build` — step 1: build knowledge graph
- `/api-doc-gen` — step 2: generate API docs (needs graph only)
- `/embed-gen` — step 3: build vector embeddings (needs graph)
- `/wiki-gen` — step 4: generate wiki (needs graph + embeddings + LLM)

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py init $ARGUMENTS
```

Supported options:
- `--rebuild` : Force rebuild even if cached data exists
- `--mode comprehensive|concise` : comprehensive = 8-10 pages (default), concise = 4-5 pages
- `--backend kuzu|memgraph|memory` : Graph database backend (default: kuzu)
- `--no-wiki` : Skip wiki generation (graph + api-docs + embeddings only)
- `--no-embed` : Skip embeddings and wiki (graph + api-docs only, fastest)

Example: `/repo-init /home/user/my-project --mode concise`

This takes 2-10 minutes depending on repo size. Progress will be printed inline.
After completion, other commands (`/graph-query`, `/code-search`, `/wiki-list`, etc.) become available.
