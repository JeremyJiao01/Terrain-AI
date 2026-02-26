Find relevant APIs by natural language description. Combines semantic search with API documentation lookup — returns matching functions along with their signatures, docstrings, and call graphs.

```bash
python3 -m code_graph_builder.commands_cli api-find "$ARGUMENTS"
```

Add `--top-k N` to control the number of results (default: 5).

Example: `/api-find user authentication logic --top-k 3`

This is a convenience command that combines `/code-search` and `/api-detail` into one step.
Requires embeddings to have been built via `/repo-init`.
