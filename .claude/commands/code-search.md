Search the codebase semantically using vector embeddings. Returns the most relevant functions/classes for the query.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py search "$ARGUMENTS"
```

Add `--top-k N` to control the number of results (default: 5).

Example: `/code-search recursive tree traversal --top-k 10`

Requires embeddings to have been built via `/repo-init`.
