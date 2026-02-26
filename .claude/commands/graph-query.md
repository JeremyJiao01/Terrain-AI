Translate a natural-language question into Cypher and execute it against the code knowledge graph.

```bash
python3 ~/.claude/commands/code-graph/cgb_cli.py query "$ARGUMENTS"
```

The command will:
1. Use the LLM to translate the question to a Cypher query
2. Execute the Cypher against the graph database
3. Return the raw results

Requires an LLM API key to be configured (LLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY).

Example: `/graph-query which functions call parse_expression?`
