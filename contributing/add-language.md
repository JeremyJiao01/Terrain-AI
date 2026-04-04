# Add a New Language

Step-by-step guide to add Tree-sitter support for a new programming language.

## Steps

### 1. Add to SupportedLanguage Enum

File: `code_graph_builder/constants.py`

Add the language to `SupportedLanguage(StrEnum)`:

```python
class SupportedLanguage(StrEnum):
    ...
    NEW_LANG = "new_lang"
```

### 2. Create Language Spec

File: `code_graph_builder/language_spec.py`

Add three items:

1. `_newlang_get_name(node: Node) -> str | None` -- extract name from AST node.
2. `_newlang_file_to_module(file_path: Path, repo_root: Path) -> list[str]` -- convert file path to module parts.
3. Register a `LanguageSpec(...)` entry in the specs dictionary, mapping `SupportedLanguage.NEW_LANG` to the helpers above plus file extensions and AST query strings.

### 3. Register in Parser Factory

File: `code_graph_builder/parsers/factory.py`

Ensure `ProcessorFactory` handles the new `SupportedLanguage` value. If the language needs custom processing logic, add it here.

### 4. Add Grammar Dependency

File: `pyproject.toml`

Add `tree-sitter-<lang>` to either:
- `dependencies` (core set, if the language is common), or
- `[project.optional-dependencies] treesitter-full` (if niche).

### 5. Write Tests

File: `code_graph_builder/tests/test_<lang>.py`

Required test cases:
- Parse a minimal source file and verify nodes are created.
- Verify function/class/method names are extracted.
- Verify cross-file call resolution if applicable.
- Verify file extensions are recognized.

### 6. Verify

```bash
# Install the new grammar
pip install -e ".[treesitter-full]"

# Check layer rules
python tools/dep_check.py

# Run tests
python -m pytest code_graph_builder/tests/test_<lang>.py -v

# Run full suite to catch regressions
python -m pytest code_graph_builder/tests/ -v
```
