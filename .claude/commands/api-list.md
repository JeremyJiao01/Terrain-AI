List public API interfaces for a module or the entire project. Returns function signatures, struct/enum definitions, and type declarations.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py list-api $ARGUMENTS
```

Options:
- `--module <name>` : Filter by module qualified name (omit for all modules)
- `--visibility public|static|extern|all` : Visibility filter (default: public)
- `--include-types` : Include struct/enum/typedef definitions (default: true)

Example: `/api-list --module project.parser --visibility all`
