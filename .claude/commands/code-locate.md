Locate a function or method in the repository using Tree-sitter AST parsing. Returns source code, line numbers, and qualified name.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py locate $ARGUMENTS
```

Arguments:
- First: relative file path from repo root
- Second: function or method name (use `ClassName.method` for methods)
- `--line N` : optional line number to disambiguate overloads

Example: `/code-locate src/parser.c parse_expression`
Example: `/code-locate src/main.py MyClass.run --line 42`
