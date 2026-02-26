Browse the hierarchical API documentation. Without arguments, returns the L1 module index. With a module name, returns the L2 module detail page.

```bash
python3 -m code_graph_builder.commands_cli api-docs $ARGUMENTS
```

Options:
- `--module <name>` : Show L2 detail for a specific module

Workflow:
1. `/api-browse` — see all modules (L1 index)
2. `/api-browse --module project.parser` — see all functions in that module (L2)
3. `/api-detail project.parser.parse_expr` — see detailed doc for one function (L3)
