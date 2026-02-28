Switch the active repository to a previously indexed one. After switching, all query commands (`/graph-query`, `/code-search`, `/wiki-list`, `/api-browse`, etc.) will operate on the selected repo.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py switch-repo $ARGUMENTS
```

The argument is the repository name or artifact directory name (use `/list-repos` to see available names).

Examples:
- `/switch-repo my-project` — switch by repo name
- `/switch-repo my-project_a1b2c3d4` — switch by artifact dir name
