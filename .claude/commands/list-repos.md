List all previously indexed repositories in the workspace. Shows each repo's name, path, last indexed time, completed pipeline steps (graph, api_docs, embeddings, wiki), and which one is currently active.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py list-repos
```

Use this at the start of a new session to discover available repos, then `/switch-repo` to activate one.
