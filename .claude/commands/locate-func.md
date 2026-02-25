Locate a function or method in the repository using Tree-sitter AST parsing. Returns source code, line numbers, and qualified name.

```bash
python3 -m code_graph_builder.commands_cli locate $ARGUMENTS
```

Arguments:
- First: relative file path from repo root
- Second: function or method name (use `ClassName.method` for methods)
- `--line N` : optional line number to disambiguate overloads

Example: `/locate-func src/parser.c parse_expression`
Example: `/locate-func src/main.py MyClass.run --line 42`
