Retrieve source code of a function, method, or class by its fully qualified name from the knowledge graph.

```bash
python3 -m code_graph_builder.commands_cli snippet $ARGUMENTS
```

The argument is the fully qualified name (e.g. `mymodule.MyClass.my_method`).

Example: `/code-snippet tinycc.tcc.tcc_compile_string`
