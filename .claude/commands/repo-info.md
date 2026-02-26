Show information about the currently active (indexed) repository, including graph statistics, wiki pages, and service availability.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py info
```

Present the JSON result in a readable format, highlighting:
- Repository path and indexing time
- Graph node/relationship counts
- Available services (Cypher query, semantic search, API docs)
- Number of wiki pages generated
