Show information about the currently active (indexed) repository, including graph statistics, wiki pages, and service availability.

```bash
python3 -m code_graph_builder.commands_cli info
```

Present the JSON result in a readable format, highlighting:
- Repository path and indexing time
- Graph node/relationship counts
- Available services (Cypher query, semantic search, API docs)
- Number of wiki pages generated
