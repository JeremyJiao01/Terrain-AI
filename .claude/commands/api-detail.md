Read detailed API documentation for a specific function. Includes signature, docstring, and full call graph (who calls it and what it calls).

```bash
python3 -m code_graph_builder.commands_cli api-doc $ARGUMENTS
```

The argument is the fully qualified function name.

Example: `/api-detail project.parser.parse_expression`

Use `/api-browse` first to browse available modules and functions.
