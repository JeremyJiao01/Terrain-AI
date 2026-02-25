Index a code repository: build knowledge graph, generate vector embeddings, and produce a multi-page wiki.

Run the following command (adjust options as needed based on user input):

```bash
python3 -m code_graph_builder.commands_cli init $ARGUMENTS
```

Supported options:
- `--rebuild` : Force rebuild even if cached data exists
- `--mode comprehensive|concise` : comprehensive = 8-10 pages (default), concise = 4-5 pages
- `--backend kuzu|memgraph|memory` : Graph database backend (default: kuzu)

Example: `/repo-init /home/user/my-project --mode concise`

This takes 2-10 minutes depending on repo size. Progress will be printed inline.
After completion, other commands (`/graph-query`, `/code-search`, `/wiki-list`, etc.) become available.
