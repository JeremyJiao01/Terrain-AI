"""Pipeline with progress callbacks for MCP: graph (+ api_docs) → embedding → wiki.

Each stage calls `progress_cb(message)` after every meaningful unit of work
so the MCP server can relay real-time updates to the client.
"""

from __future__ import annotations

import json
import os
import pickle
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from code_graph_builder.domains.upper.rag.client import create_llm_client, LLMClient
except ImportError:
    create_llm_client = None  # type: ignore[assignment,misc]
    LLMClient = None  # type: ignore[assignment,misc]

ProgressCb = Callable[[str, float], None] | None
"""Progress callback: (message, percentage_0_to_100) -> None.

Pipeline weight allocation:
    Step 1 (graph + API docs):  0 – 15 %
    Step 2 (embeddings):       15 – 40 %
    Step 3 (wiki generation):  40 – 100 %
"""


# ---------------------------------------------------------------------------
# Helpers shared with generate_wiki.py
# ---------------------------------------------------------------------------

def _resolve_source_file(qname: str, repo_path: Path) -> Path | None:
    parts = qname.split(".")
    if len(parts) < 3:
        return None
    dir_parts = parts[1:-1]
    for depth in range(len(dir_parts), 0, -1):
        for suffix in (".c", ".py", ".h", ".cpp", ".go", ".rs", ".js", ".ts"):
            candidate = repo_path.joinpath(*dir_parts[:depth]).with_suffix(suffix)
            if candidate.exists():
                return candidate
    return None


_MAX_SOURCE_CHARS = 2000


def _read_function_source(func: dict, repo_path: Path) -> str | None:
    qname = func.get("qualified_name", "")
    start_line = func.get("start_line", 0)
    end_line = func.get("end_line", 0)
    if start_line == 0 or start_line == end_line:
        return None
    file_path = _resolve_source_file(qname, repo_path)
    if file_path is None:
        return None
    try:
        from code_graph_builder.foundation.utils.encoding import read_source_file
        content = read_source_file(file_path)
        lines = content.splitlines(keepends=True)
        source = "".join(lines[start_line - 1: end_line])
        if len(source) > _MAX_SOURCE_CHARS:
            source = source[:_MAX_SOURCE_CHARS] + "\n    /* ... truncated ... */"
        return source
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Step 1: graph build + API docs generation
# ---------------------------------------------------------------------------

_FUNC_DOC_QUERY = """
    MATCH (f:Function)
    WHERE f.qualified_name IS NOT NULL
    OPTIONAL MATCH (m:Module)-[]->(f)
    RETURN DISTINCT
           COALESCE(m.qualified_name, 'unknown') AS module_qn,
           COALESCE(m.path, f.path) AS module_path,
           f.qualified_name, f.name, f.signature, f.return_type,
           f.visibility, f.parameters, f.docstring,
           f.start_line, f.end_line, f.path, f.kind
    ORDER BY module_qn, f.start_line
"""

_TYPE_DOC_QUERY_CLASS = """
    MATCH (c:Class)
    RETURN DISTINCT c.qualified_name, c.name, c.kind, c.signature,
           c.parameters, c.start_line, c.end_line
    ORDER BY c.qualified_name, c.start_line
"""

_TYPE_DOC_QUERY_TYPE = """
    MATCH (t:Type)
    RETURN DISTINCT t.qualified_name, t.name, t.kind, t.signature,
           t.start_line, t.end_line
    ORDER BY t.qualified_name, t.start_line
"""

_CALLS_QUERY = """
    MATCH (caller:Function)-[:CALLS]->(callee:Function)
    RETURN DISTINCT caller.qualified_name, callee.qualified_name,
           callee.path, callee.start_line,
           caller.path, caller.start_line, caller.end_line
"""


def build_graph(
    repo_path: Path,
    db_path: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
    backend: str = "kuzu",
) -> Any:
    """Build or reuse a code knowledge graph.

    This step only creates the graph database.  API docs, embeddings, and
    wiki generation are separate steps.
    """
    from code_graph_builder.domains.core.graph.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(
        repo_path=str(repo_path),
        backend=backend,
        backend_config={"db_path": str(db_path), "batch_size": 1000},
    )

    if rebuild or not db_path.exists():
        result = builder.build_graph(clean=rebuild)
        if progress_cb:
            progress_cb(
                f"Graph built: "
                f"{result.nodes_created} nodes, "
                f"{result.relationships_created} relationships, "
                f"{result.files_processed} files processed.",
                10.0,
            )
    else:
        stats = builder.get_statistics()
        if progress_cb:
            progress_cb(
                f"Reusing existing graph: "
                f"{stats.get('node_count', '?')} nodes, "
                f"{stats.get('relationship_count', '?')} relationships.",
                10.0,
            )

    return builder


# ---------------------------------------------------------------------------
# Step 2: API docs generation (graph-only, no embeddings needed)
# ---------------------------------------------------------------------------

def generate_api_docs_step(
    builder: Any,
    artifact_dir: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
    repo_path: Path | None = None,
) -> dict[str, Any]:
    """Generate hierarchical API docs from the knowledge graph.

    Requires only a populated graph database — no embeddings or LLM needed.
    """
    from code_graph_builder.domains.upper.apidoc.api_doc_generator import generate_api_docs

    api_dir = artifact_dir / "api_docs"
    index_file = api_dir / "index.md"

    if not rebuild and index_file.exists():
        if progress_cb:
            progress_cb("Reusing cached API docs.", 15.0)
        return {"status": "cached"}

    try:
        func_rows = builder.query(_FUNC_DOC_QUERY)
        type_rows = builder.query(_TYPE_DOC_QUERY_CLASS) + builder.query(_TYPE_DOC_QUERY_TYPE)
        call_rows = builder.query(_CALLS_QUERY)
    except Exception as exc:
        msg = f"API docs skipped — graph query failed: {exc}"
        logger.warning(msg)
        if progress_cb:
            progress_cb(msg, 15.0)
        return {"status": "skipped", "error": str(exc)}

    result = generate_api_docs(
        func_rows, type_rows, call_rows, artifact_dir, repo_path=repo_path,
    )
    if progress_cb:
        progress_cb(
            f"API docs generated: "
            f"{result['module_count']} modules, "
            f"{result['func_count']} functions, "
            f"{result['type_count']} types.",
            15.0,
        )
    return {"status": "success", **result}


def validate_api_docs(artifact_dir: Path) -> dict[str, Any]:
    """Validate that generated API docs meet minimum quality standards.

    Checks:
        1. ``api_docs/index.md`` exists and is non-empty.
        2. ``api_docs/modules/`` contains at least one ``.md`` file.
        3. ``api_docs/funcs/`` contains at least one ``.md`` file.

    Returns:
        A dict with ``valid`` (bool), ``issues`` (list of strings),
        and counts for ``modules``, ``funcs``.
    """
    api_dir = artifact_dir / "api_docs"
    index_file = api_dir / "index.md"
    modules_dir = api_dir / "modules"
    funcs_dir = api_dir / "funcs"

    issues: list[str] = []

    if not index_file.exists():
        issues.append("index.md does not exist")
    elif index_file.stat().st_size == 0:
        issues.append("index.md is empty")

    module_count = 0
    if modules_dir.exists():
        module_count = len(list(modules_dir.glob("*.md")))
    if module_count == 0:
        issues.append("No module documentation files found in api_docs/modules/")

    func_count = 0
    if funcs_dir.exists():
        func_count = len(list(funcs_dir.glob("*.md")))
    if func_count == 0:
        issues.append("No function documentation files found in api_docs/funcs/")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "modules": module_count,
        "funcs": func_count,
    }


# ---------------------------------------------------------------------------
# Step 1b: LLM-powered description generation for undocumented functions
# ---------------------------------------------------------------------------

_DESC_SYSTEM_PROMPT = """\
You are a code documentation assistant. Given a function's signature, \
source code, module context, call relationships, and usage examples, \
generate a BILINGUAL description (Chinese and English) describing what \
the function does and how it is typically used. \

Format your response EXACTLY as follows:
中文：<concise Chinese description>
English：<concise English description>

Focus on the function's PURPOSE and its role in the codebase, not low-level \
implementation details. Do NOT include the function name in the description. \
Both descriptions should convey the same meaning but be natural in each language."""

_DESC_BATCH_SIZE = 10


def _build_desc_prompt(funcs: list[dict]) -> str:
    """Build a batched prompt for multiple functions.

    Includes caller information and usage examples extracted from the
    L3 Markdown if available, giving the LLM richer context about how
    each function is actually used in the codebase.
    """
    parts: list[str] = []
    for i, f in enumerate(funcs):
        sig = f.get("signature") or f.get("name", "unknown")
        source = f.get("source", "")
        module = f.get("module_qn", "")
        callers = f.get("callers", "")
        usage = f.get("usage_example", "")

        entry = (
            f"[{i+1}] Module: {module}\n"
            f"    Signature: {sig}\n"
        )
        if callers:
            entry += f"    Called by: {callers}\n"
        if usage:
            entry += f"    Usage example:\n{usage}\n"
        entry += f"    Source:\n{source}\n"
        parts.append(entry)

    parts.append(
        f"\nGenerate exactly {len(funcs)} descriptions, one per line, "
        f"numbered [1] to [{len(funcs)}]. Each description should be a "
        f"single concise sentence that reflects the function's purpose "
        f"and how it is used by its callers."
    )
    return "\n".join(parts)


def _parse_desc_response(response: str, count: int) -> list[str]:
    """Parse numbered descriptions from LLM response.

    Supports bilingual descriptions in the format:
    [N] 中文：<Chinese description> English：<English description>
    """
    import re

    descriptions: list[str] = [""] * count
    current_idx = -1
    current_desc_lines = []

    for line in response.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        # Match "[N] desc", "N. desc", or "N) desc" with regex
        m = re.match(r"^\[?(\d+)[.\)\]]\s*(.*)", line)
        if m:
            # Save previous description if exists
            if 0 <= current_idx < count and current_desc_lines:
                descriptions[current_idx] = " ".join(current_desc_lines).strip()

            idx = int(m.group(1)) - 1  # 1-based to 0-based
            current_idx = idx
            current_desc_lines = [m.group(2).strip()] if m.group(2).strip() else []
        elif current_idx >= 0:
            # Continuation of previous description (for multi-line descriptions)
            current_desc_lines.append(line)

    # Save the last description
    if 0 <= current_idx < count and current_desc_lines:
        descriptions[current_idx] = " ".join(current_desc_lines).strip()

    return descriptions


_DESC_MAX_CONSECUTIVE_FAILURES = 3  # Circuit breaker: stop after N consecutive batch failures


def _collect_todo_funcs(funcs_dir: Path) -> list[dict]:
    """Scan L3 doc files and collect those with TODO placeholders."""
    todo_funcs: list[dict] = []

    for md_file in sorted(funcs_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        if "<!-- TODO:" not in content:
            continue

        func_info: dict = {"path": md_file, "content": content}
        for line in content.splitlines():
            if line.startswith("# "):
                func_info["name"] = line[2:].strip()
            elif line.startswith("- 签名:") or line.startswith("- 定义:"):
                start = line.find("`")
                end = line.rfind("`")
                if start != -1 and end > start:
                    func_info["signature"] = line[start + 1 : end]
            elif line.startswith("- 模块:"):
                func_info["module_qn"] = (
                    line[len("- 模块:") :].strip().split(" —")[0].strip()
                )

        if "## 实现" in content:
            source_start = content.index("## 实现")
            code_start = content.find("```", source_start)
            code_end = (
                content.find("```", code_start + 3) if code_start != -1 else -1
            )
            if code_start != -1 and code_end != -1:
                first_newline = content.index("\n", code_start)
                func_info["source"] = content[first_newline + 1 : code_end].strip()

        if "## 被调用" in content:
            called_by_start = content.index("## 被调用")
            caller_names: list[str] = []
            for cline in content[called_by_start:].splitlines()[2:]:
                cline = cline.strip()
                if not cline or cline.startswith("#"):
                    break
                if cline.startswith("- "):
                    cname = cline[2:].split("(")[0].split("→")[0].strip()
                    if "." in cname:
                        cname = cname.rsplit(".", 1)[-1]
                    if cname and cname != "*(无调用者)*":
                        caller_names.append(cname)
            if caller_names:
                func_info["callers"] = ", ".join(caller_names[:5])

        if "## 使用示例" in content:
            usage_start = content.index("## 使用示例")
            usage_code_start = content.find("```", usage_start)
            usage_code_end = (
                content.find("```", usage_code_start + 3) if usage_code_start != -1 else -1
            )
            if usage_code_start != -1 and usage_code_end != -1:
                first_nl = content.index("\n", usage_code_start)
                usage_snippet = content[first_nl + 1 : usage_code_end].strip()
                if len(usage_snippet) > 500:
                    usage_snippet = usage_snippet[:500] + "\n    /* ... */"
                func_info["usage_example"] = usage_snippet

        if "name" in func_info:
            todo_funcs.append(func_info)

    return todo_funcs


def generate_descriptions_step(
    artifact_dir: Path,
    repo_path: Path,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    """Generate LLM descriptions for functions missing docstrings.

    Reads L3 API doc files, finds those with TODO placeholders,
    generates descriptions via LLM, and writes them back.

    **Resumable**: completed descriptions are written to disk immediately.
    Files whose TODO placeholder has already been replaced are skipped on
    re-scan, so the function can be called again after an interruption and
    will automatically continue from where it left off.

    **Circuit breaker**: after ``_DESC_MAX_CONSECUTIVE_FAILURES`` consecutive
    batch failures the function stops early to avoid wasting quota on an
    unresponsive provider.

    **Graceful interrupt**: ``KeyboardInterrupt`` is caught so that progress
    made before the interrupt is preserved.

    Returns:
        Summary dict with generated_count, skipped_count, error_count,
        and interrupted (bool).
    """
    if create_llm_client is None:
        logger.info("LLM client not available, skipping description generation")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0, "interrupted": False}

    try:
        client = create_llm_client()
    except (ValueError, RuntimeError) as e:
        logger.info(f"No LLM API key configured, skipping description generation: {e}")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0, "interrupted": False}

    funcs_dir = artifact_dir / "api_docs" / "funcs"
    if not funcs_dir.exists():
        logger.warning("No API docs found, skipping description generation")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0, "interrupted": False}

    todo_funcs = _collect_todo_funcs(funcs_dir)

    if not todo_funcs:
        logger.info("All functions already have descriptions")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0, "interrupted": False}

    total = len(todo_funcs)
    logger.info(f"Generating descriptions for {total} functions")

    generated = 0
    errors = 0
    consecutive_failures = 0
    interrupted = False
    total_batches = (total + _DESC_BATCH_SIZE - 1) // _DESC_BATCH_SIZE

    for batch_idx in range(0, total, _DESC_BATCH_SIZE):
        batch = todo_funcs[batch_idx : batch_idx + _DESC_BATCH_SIZE]
        current_batch = batch_idx // _DESC_BATCH_SIZE + 1

        if progress_cb:
            pct = int(current_batch / total_batches * 100)
            progress_cb(
                f"Generating descriptions: batch {current_batch}/{total_batches} "
                f"({generated} done, {errors} errors, {total - generated - errors} remaining)",
                float(pct),
            )

        prompt = _build_desc_prompt(batch)

        try:
            response = client.chat(
                query=prompt,
                system_prompt=_DESC_SYSTEM_PROMPT,
                max_tokens=4096,
                temperature=1.0,
            )

            descriptions = _parse_desc_response(response.content, len(batch))

            batch_ok = 0
            for func_info, desc in zip(batch, descriptions):
                if not desc:
                    errors += 1
                    continue

                old_content = func_info["content"]
                new_content = ""
                for line in old_content.splitlines(keepends=True):
                    if "<!-- TODO:" in line and "-->" in line:
                        new_content += f"> {desc}\n"
                    else:
                        new_content += line

                func_info["path"].write_text(new_content, encoding="utf-8")
                generated += 1
                batch_ok += 1

            # Reset circuit breaker on any success
            if batch_ok > 0:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

        except KeyboardInterrupt:
            logger.warning(
                f"Interrupted by user after batch {current_batch}/{total_batches}. "
                f"Progress saved: {generated} generated, {errors} errors. "
                f"Run again to resume from remaining {total - generated - errors} functions."
            )
            interrupted = True
            break

        except Exception as e:
            logger.warning(f"LLM batch {current_batch}/{total_batches} failed: {e}")
            errors += len(batch)
            consecutive_failures += 1

        # Circuit breaker: stop if provider appears down
        if consecutive_failures >= _DESC_MAX_CONSECUTIVE_FAILURES:
            logger.error(
                f"Circuit breaker tripped: {consecutive_failures} consecutive batch failures. "
                f"Stopping early. Progress saved: {generated} generated. "
                f"Run again to resume from remaining {total - generated - errors} functions."
            )
            interrupted = True
            break

    remaining = total - generated - errors
    logger.info(
        f"Description generation {'interrupted' if interrupted else 'complete'}: "
        f"{generated} generated, {errors} errors, {remaining} remaining"
    )
    return {
        "generated_count": generated,
        "skipped_count": remaining,
        "error_count": errors,
        "interrupted": interrupted,
    }


# ---------------------------------------------------------------------------
# Step 1c: LLM-powered module summaries and usage workflows
# ---------------------------------------------------------------------------

_ENHANCE_BATCH_SIZE = 5  # Modules per LLM call

_MODULE_SUMMARY_SYSTEM = """\
You are a senior software architect. Given a module's function list with \
signatures and brief descriptions, generate:
1. A one-sentence summary of the module's purpose (field: summary).
2. If the module has public API functions that are commonly used together, \
   describe 1-3 typical usage workflows as numbered steps. Each step should \
   name the function and briefly explain what it does in that context. \
   If no clear workflow exists, return an empty list (field: workflows).

Reply in JSON format:
{"summary": "...", "workflows": ["1. call_a() — do X\\n2. call_b() — do Y", ...]}
Reply with ONLY the JSON, nothing else."""


def _parse_module_content(module_path: Path) -> dict[str, Any] | None:
    """Parse an L2 module Markdown to extract function signatures."""
    content = module_path.read_text(encoding="utf-8")
    if len(content) < 50:
        return None

    module_qn = ""
    funcs: list[str] = []

    for line in content.splitlines():
        if line.startswith("# ") and not module_qn:
            module_qn = line[2:].strip()
        # Table rows: | [func_name](...) | `signature` | description |
        if line.startswith("| ["):
            parts = line.split("|")
            if len(parts) >= 4:
                name_part = parts[1].strip()
                sig_part = parts[2].strip().strip("`")
                desc_part = parts[3].strip()
                # Extract function name from [name](link)
                if "](" in name_part:
                    fname = name_part.split("](")[0].lstrip("[")
                else:
                    fname = name_part
                entry = f"{fname}: {sig_part}"
                if desc_part and desc_part != "—":
                    entry += f" — {desc_part}"
                funcs.append(entry)

    if not module_qn or not funcs:
        return None

    return {
        "module_qn": module_qn,
        "path": module_path,
        "content": content,
        "funcs": funcs,
    }


def enhance_api_docs_step(
    artifact_dir: Path,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    """Generate module summaries and usage workflows via LLM.

    Reads L2 module pages, sends function lists to LLM, writes back:
    - Module summary into L1 index (replaces "—" in 职责 column)
    - Usage workflows appended to L2 module page

    Resumable: modules with existing summaries in the L1 index are skipped.

    Returns summary dict with generated_count, skipped_count, error_count.
    """
    if create_llm_client is None:
        logger.info("LLM client not available, skipping enhancement")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    try:
        client = create_llm_client()
    except (ValueError, RuntimeError) as e:
        logger.info(f"No LLM API key configured, skipping enhancement: {e}")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    modules_dir = artifact_dir / "api_docs" / "modules"
    index_file = artifact_dir / "api_docs" / "index.md"
    if not modules_dir.exists() or not index_file.exists():
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    # Read current index to check which modules already have summaries
    index_content = index_file.read_text(encoding="utf-8")
    # Parse index table: | [module](link) | summary | ...
    already_done: set[str] = set()
    for line in index_content.splitlines():
        if line.startswith("| ["):
            parts = line.split("|")
            if len(parts) >= 3:
                summary = parts[2].strip()
                if summary and summary != "—":
                    # Extract module name from [name](link)
                    name_part = parts[1].strip()
                    if "](" in name_part:
                        mname = name_part.split("](")[0].lstrip("[")
                        already_done.add(mname)

    # Collect modules needing enhancement
    todo_modules: list[dict] = []
    for md_file in sorted(modules_dir.glob("*.md")):
        parsed = _parse_module_content(md_file)
        if parsed and parsed["module_qn"] not in already_done:
            # Skip very small modules (< 3 functions) — not worth LLM call
            if len(parsed["funcs"]) >= 3:
                todo_modules.append(parsed)

    if not todo_modules:
        logger.info("All modules already have summaries")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    total = len(todo_modules)
    logger.info(f"Enhancing {total} modules with LLM summaries and workflows")

    generated = 0
    errors = 0
    consecutive_failures = 0
    interrupted = False
    total_batches = (total + _ENHANCE_BATCH_SIZE - 1) // _ENHANCE_BATCH_SIZE

    # Collect results to batch-update index at the end
    summaries: dict[str, str] = {}  # module_qn → summary

    for batch_idx in range(0, total, _ENHANCE_BATCH_SIZE):
        batch = todo_modules[batch_idx : batch_idx + _ENHANCE_BATCH_SIZE]
        current_batch = batch_idx // _ENHANCE_BATCH_SIZE + 1

        if progress_cb:
            pct = int(current_batch / total_batches * 100)
            progress_cb(
                f"Enhancing modules: batch {current_batch}/{total_batches} "
                f"({generated} done, {errors} errors)",
                float(pct),
            )

        # Build prompt for this batch
        prompt_parts: list[str] = []
        for i, mod in enumerate(batch):
            func_list = "\n".join(f"  - {f}" for f in mod["funcs"][:30])
            prompt_parts.append(
                f"[{i+1}] Module: {mod['module_qn']}\n"
                f"Functions ({len(mod['funcs'])}):\n{func_list}\n"
            )
        prompt = (
            "\n".join(prompt_parts)
            + f"\nGenerate exactly {len(batch)} JSON objects, one per line, "
            f"numbered [1] to [{len(batch)}]."
        )

        try:
            response = client.chat(
                query=prompt,
                system_prompt=_MODULE_SUMMARY_SYSTEM,
                max_tokens=4096,
                temperature=1.0,
            )

            # Parse response: expect [N] {json} per line
            import re as _re
            for line in response.content.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                m = _re.match(r"^\[?(\d+)[.\)\]]\s*(.*)", line)
                if not m:
                    continue
                idx = int(m.group(1)) - 1
                json_str = m.group(2).strip()
                if 0 <= idx < len(batch):
                    try:
                        data = json.loads(json_str)
                    except json.JSONDecodeError:
                        # Try extracting JSON from the line
                        json_start = json_str.find("{")
                        json_end = json_str.rfind("}") + 1
                        if json_start != -1 and json_end > json_start:
                            try:
                                data = json.loads(json_str[json_start:json_end])
                            except json.JSONDecodeError:
                                errors += 1
                                continue
                        else:
                            errors += 1
                            continue

                    mod = batch[idx]
                    summary = data.get("summary", "").strip()
                    workflows = data.get("workflows", [])

                    if summary:
                        summaries[mod["module_qn"]] = summary

                        # Append workflows to L2 module page if any
                        if workflows:
                            workflow_section = "\n\n## 使用工作流\n"
                            for wf in workflows:
                                workflow_section += f"\n{wf}\n"

                            old_content = mod["content"]
                            # Only add if not already present
                            if "## 使用工作流" not in old_content:
                                new_content = old_content.rstrip() + workflow_section + "\n"
                                mod["path"].write_text(new_content, encoding="utf-8")

                        generated += 1
                    else:
                        errors += 1

            consecutive_failures = 0 if generated > 0 else consecutive_failures + 1

        except KeyboardInterrupt:
            logger.warning(f"Interrupted. {generated} modules enhanced.")
            interrupted = True
            break
        except Exception as e:
            logger.warning(f"LLM enhancement batch {current_batch} failed: {e}")
            errors += len(batch)
            consecutive_failures += 1

        if consecutive_failures >= _DESC_MAX_CONSECUTIVE_FAILURES:
            logger.error(f"Circuit breaker: {consecutive_failures} consecutive failures.")
            interrupted = True
            break

    # Update L1 index with summaries
    if summaries:
        new_index_lines: list[str] = []
        for line in index_content.splitlines():
            if line.startswith("| ["):
                parts = line.split("|")
                if len(parts) >= 3:
                    name_part = parts[1].strip()
                    if "](" in name_part:
                        mname = name_part.split("](")[0].lstrip("[")
                        if mname in summaries:
                            parts[2] = f" {summaries[mname]} "
                            line = "|".join(parts)
            new_index_lines.append(line)
        index_file.write_text("\n".join(new_index_lines), encoding="utf-8")

    logger.info(f"Enhancement complete: {generated} modules, {errors} errors")
    return {
        "generated_count": generated,
        "skipped_count": total - generated - errors,
        "error_count": errors,
        "interrupted": interrupted,
    }


# ---------------------------------------------------------------------------
# Step 3: vector index with per-batch progress
# ---------------------------------------------------------------------------

_EMBED_BATCH_SIZE = 10


def _parse_l3_for_embedding(md_path: Path) -> tuple[dict, str] | None:
    """Parse an L3 API doc Markdown file and extract structured info + embedding text.

    Returns (func_info, embedding_text) or None if the file is too small.
    The embedding text is a de-formatted semantic string optimised for vector
    retrieval — Markdown formatting noise is stripped so the embedding model
    receives only meaningful semantic tokens.
    """
    content = md_path.read_text(encoding="utf-8")
    if len(content) < 30:
        return None

    func_info: dict[str, Any] = {}
    lines = content.splitlines()

    # ---- Parse header metadata ----
    for line in lines:
        if line.startswith("# "):
            func_info["name"] = line[2:].strip()
        elif line.startswith("> ") and "name" in func_info and "description" not in func_info:
            desc = line[2:].strip()
            if not desc.startswith("<!--"):
                func_info["description"] = desc
        elif line.startswith("- 签名:") or line.startswith("- 定义:"):
            start = line.find("`")
            end = line.rfind("`")
            if start != -1 and end > start:
                func_info["signature"] = line[start + 1 : end]
        elif line.startswith("- 返回:"):
            start = line.find("`")
            end = line.rfind("`")
            if start != -1 and end > start:
                func_info["return_type"] = line[start + 1 : end]
        elif line.startswith("- 可见性:"):
            func_info["visibility"] = line[len("- 可见性:"):].strip().split("|")[0].strip()
        elif line.startswith("- 位置:"):
            func_info["location"] = line[len("- 位置:"):].strip()
        elif line.startswith("- 模块:"):
            func_info["module"] = line[len("- 模块:"):].strip()
        elif line.startswith("- 类型: 宏定义"):
            func_info["kind"] = "macro"

    # Derive qualified_name from filename (filename = sanitised qn)
    stem = md_path.stem  # e.g. "tinycc.tccgen.gv"
    func_info["qualified_name"] = stem

    if not func_info.get("name"):
        return None

    # Infer kind if not already set
    if "kind" not in func_info:
        func_info["kind"] = "function"

    # ---- Split content into sections for priority-based truncation ----
    sections = _split_into_sections(lines)

    embedding_text = _build_embedding_text(func_info, sections)

    return func_info, embedding_text


def _split_into_sections(lines: list[str]) -> dict[str, list[str]]:
    """Split L3 Markdown lines into named sections.

    Returns a dict mapping section names to their content lines.
    Recognised sections: header, description, call_tree, callers,
    usage_examples, params, source.
    """
    sections: dict[str, list[str]] = {"header": []}
    current = "header"

    section_map = {
        "## 描述": "description",
        "## 调用树": "call_tree",
        "## 被调用": "callers",
        "## 使用示例": "usage_examples",
        "## 参数与内存": "params",
        "## 实现": "source",
    }

    for line in lines:
        matched = False
        for prefix, name in section_map.items():
            if line.startswith(prefix):
                current = name
                sections.setdefault(current, [])
                matched = True
                break
        if not matched:
            sections.setdefault(current, [])
            sections[current].append(line)

    return sections


def _build_embedding_text(
    func_info: dict[str, Any],
    sections: dict[str, list[str]],
    max_chars: int = 4000,
) -> str:
    """Build a de-formatted semantic text optimised for embedding retrieval.

    Strips Markdown syntax (headings, backticks, table borders, fences) and
    assembles a plain-text representation ordered by semantic importance.

    Supports bilingual (Chinese/English) descriptions for better cross-language
    semantic search.

        1. Identity + bilingual description  (always included)
        2. Call tree               (high value for structural queries)
        3. Callers                 (medium value)
        4. Source code             (lowest priority, truncated first)

    The result is capped at *max_chars* by dropping lower-priority sections
    from the tail rather than cutting mid-sentence.
    """
    parts: list[str] = []

    # --- 1. Identity block (always included) ---
    name = func_info.get("name", "")
    sig = func_info.get("signature", name)
    kind = func_info.get("kind", "function")
    module = func_info.get("module", "")
    ret = func_info.get("return_type", "")

    identity = f"[{kind}] {sig}"
    if ret:
        identity += f" -> {ret}"
    parts.append(identity)

    if module:
        parts.append(f"模块: {module}")

    # --- 2. Bilingual descriptions (high priority for cross-language search) ---
    # Extract Chinese and English descriptions from description section
    desc_text = ""
    if "description" in sections:
        desc_text = _strip_markdown("\n".join(sections["description"])).strip()

    # Also check header description (the "> " quote line)
    header_desc = func_info.get("description", "")

    # Combine and parse bilingual content
    combined_desc = f"{header_desc}\n{desc_text}".strip()

    # Look for explicit bilingual markers
    chinese_match = None
    english_match = None

    # Try to find "中文：" or "Chinese:" patterns
    import re
    chinese_patterns = re.findall(r'(?:中文[:：]\s*)([^\n]+(?:\n(?![中文英文]:).*)*)', combined_desc, re.MULTILINE)
    english_patterns = re.findall(r'(?:English[:：]\s*)([^\n]+(?:\n(?![中文英文]:).*)*)', combined_desc, re.MULTILINE)

    if chinese_patterns:
        chinese_match = chinese_patterns[0].strip()
    if english_patterns:
        english_match = english_patterns[0].strip()

    # If no explicit markers, treat first non-English line as Chinese, rest as English
    if not chinese_match and not english_match and combined_desc:
        lines = combined_desc.split('\n')
        chinese_lines = []
        english_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Simple heuristic: if line contains Chinese characters, it's Chinese
            if any('\u4e00' <= c <= '\u9fff' for c in line):
                chinese_lines.append(line)
            else:
                english_lines.append(line)

        if chinese_lines:
            chinese_match = ' '.join(chinese_lines)
        if english_lines:
            english_match = ' '.join(english_lines)

    # Add bilingual descriptions to embedding text
    if chinese_match:
        parts.append(f"描述: {chinese_match}")
    if english_match:
        parts.append(f"Description: {english_match}")

    # Fallback: add raw description if parsing failed
    if not chinese_match and not english_match and combined_desc:
        parts.append(combined_desc)

    # --- 2. Call tree (high value for "what does X call" queries) ---
    if "call_tree" in sections:
        tree_text = _strip_markdown("\n".join(sections["call_tree"])).strip()
        if tree_text:
            parts.append(f"调用: {tree_text}")

    # --- 3. Callers (medium value for "who calls X" queries) ---
    if "callers" in sections:
        caller_lines = []
        for line in sections["callers"]:
            stripped = line.strip()
            if stripped.startswith("- ") and stripped != "*(无调用者)*":
                caller_lines.append(stripped[2:].split("→")[0].strip())
        if caller_lines:
            parts.append(f"被调用: {', '.join(caller_lines)}")

    # --- 4. Source code (lowest priority) ---
    if "source" in sections:
        source_lines = []
        in_fence = False
        for line in sections["source"]:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                source_lines.append(line)
        if source_lines:
            parts.append("源码:\n" + "\n".join(source_lines))

    # --- Priority-based truncation ---
    result = "\n".join(parts)
    if len(result) <= max_chars:
        return result

    # Drop sections from the tail until we fit
    while len(parts) > 1 and len("\n".join(parts)) > max_chars:
        parts.pop()

    return "\n".join(parts)[:max_chars]


def _strip_markdown(text: str) -> str:
    """Remove common Markdown formatting tokens from *text*.

    Strips: heading markers, backtick fences/inline code, bullet prefixes,
    table borders, blockquote markers, and HTML comments.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(line)
            continue
        # Skip table borders and HTML comments
        if line.strip().startswith("|--") or line.strip().startswith("<!--"):
            continue
        # Strip heading markers
        stripped = line.lstrip("#").strip()
        # Strip blockquote markers
        if stripped.startswith("> "):
            stripped = stripped[2:]
        # Strip bullet prefix
        if stripped.startswith("- "):
            stripped = stripped[2:]
        # Strip inline backticks
        stripped = stripped.replace("`", "")
        if stripped:
            out.append(stripped)
    return "\n".join(out)


def build_vector_index(
    builder: Any,
    repo_path: Path,
    vectors_path: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
) -> tuple[Any, Any, dict[int, dict]]:
    """Build or load vector embeddings from L3 API doc Markdown files.

    No graph database access needed — reads directly from api_docs/funcs/*.md.
    The ``builder`` parameter is kept for backward compatibility but is not used.
    """
    from code_graph_builder.domains.core.embedding.qwen3_embedder import create_embedder
    from code_graph_builder.domains.core.embedding.vector_store import MemoryVectorStore, VectorRecord

    embedder = create_embedder(batch_size=_EMBED_BATCH_SIZE)

    if not rebuild and vectors_path.exists():
        with open(vectors_path, "rb") as fh:
            cache = pickle.load(fh)
        vector_store: MemoryVectorStore = cache["vector_store"]
        func_map: dict[int, dict] = cache["func_map"]
        if progress_cb:
            progress_cb(
                f"Loaded {len(vector_store)} embeddings from cache.",
                40.0,
            )
        return vector_store, embedder, func_map

    # ---- Read from L3 API doc files (no Kuzu needed) ----
    # artifact_dir is vectors_path's parent
    funcs_dir = vectors_path.parent / "api_docs" / "funcs"
    if not funcs_dir.exists():
        logger.warning("No API docs found for embedding. Run generate_api_docs first.")
        vector_store = MemoryVectorStore(dimension=embedder.get_embedding_dimension())
        return vector_store, embedder, {}

    embeddable: list[tuple[int, dict, str]] = []
    for i, md_file in enumerate(sorted(funcs_dir.glob("*.md"))):
        parsed = _parse_l3_for_embedding(md_file)
        if parsed:
            func_info, text = parsed
            embeddable.append((i, func_info, text))

    total = len(embeddable)
    if progress_cb:
        progress_cb(
            f"Embedding {total} functions "
            f"(batch size {_EMBED_BATCH_SIZE}, "
            f"{(total + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE} API calls)...",
            16.0,
        )

    vector_store = MemoryVectorStore(dimension=embedder.get_embedding_dimension())
    func_map: dict[int, dict] = {}
    records: list[VectorRecord] = []

    for batch_start in range(0, total, _EMBED_BATCH_SIZE):
        batch = embeddable[batch_start : batch_start + _EMBED_BATCH_SIZE]
        batch_texts = [t for _, _, t in batch]

        batch_embeddings = embedder.embed_batch(batch_texts)

        if batch_embeddings is None:
            # logger.warning(
            #     "embed_batch returned None for batch at offset {}. "
            #     "Skipping {} texts.",
            #     batch_start, len(batch_texts),
            # )
            continue

        if len(batch_embeddings) != len(batch):
            # logger.warning(
            #     "embed_batch returned {} embeddings for {} inputs at offset {}. "
            #     "Skipping mismatched batch.",
            #     len(batch_embeddings), len(batch), batch_start,
            # )
            continue

        for (node_id, func, _), embedding in zip(batch, batch_embeddings):
            records.append(VectorRecord(
                node_id=node_id,
                qualified_name=func["qualified_name"],
                embedding=embedding,
                metadata={
                    "name": func.get("name", ""),
                    "location": func.get("location", ""),
                    "module": func.get("module", ""),
                    "kind": func.get("kind", "function"),
                    "visibility": func.get("visibility", ""),
                    "signature": func.get("signature", ""),
                },
            ))
            func_map[node_id] = func

        done = min(batch_start + _EMBED_BATCH_SIZE, total)
        if progress_cb and total > 0:
            overall_pct = 16.0 + (done / total) * 24.0
            progress_cb(
                f"Embedded {done}/{total} functions.",
                overall_pct,
            )

    vector_store.store_embeddings_batch(records)

    with open(vectors_path, "wb") as fh:
        pickle.dump({"vector_store": vector_store, "func_map": func_map}, fh)

    if progress_cb:
        progress_cb(f"Done — {len(records)} embeddings saved.", 40.0)

    return vector_store, embedder, func_map


# ---------------------------------------------------------------------------
# Step 3: wiki generation with per-page progress
# ---------------------------------------------------------------------------

def run_wiki_generation(
    builder: Any,
    repo_path: Path,
    output_dir: Path,
    max_pages: int,
    rebuild: bool,
    comprehensive: bool,
    vector_store: Any,
    embedder: Any,
    func_map: dict[int, dict],
    progress_cb: ProgressCb = None,
) -> tuple[Path, int]:
    """Two-phase wiki generation with per-page progress callbacks."""
    import re
    from datetime import datetime

    from code_graph_builder.examples.generate_wiki import (
        MAX_MERMAID_FIX_ATTEMPTS,
        build_source_context,
        fix_mermaid_errors,
        plan_wiki_structure,
        generate_page_content,
        semantic_search_funcs,
        validate_mermaid_blocks,
    )
    from code_graph_builder.domains.upper.rag.camel_agent import CamelAgent
    from code_graph_builder.domains.upper.rag.llm_backend import create_llm_backend

    project_name = repo_path.name
    output_dir.mkdir(parents=True, exist_ok=True)

    structure_cache = output_dir / f"{project_name}_structure.pkl"

    llm_backend = create_llm_backend(temperature=1.0)

    if not llm_backend.available:
        if progress_cb:
            progress_cb(
                "[Step 3/3] Skipped — no LLM API key configured. "
                "Set LLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY to enable wiki generation.",
                100.0,
            )
        return output_dir / "index.md", 0
    agent = CamelAgent(
        role=f"{project_name} 技术文档专家",
        goal=f"结合真实源码，为 {project_name} 生成专业、准确、图文并茂的技术 Wiki",
        backstory=f"拥有丰富的技术写作和代码阅读经验，深入理解 {project_name} 源码架构",
    )

    # Phase 1: plan structure (or load cache)
    if not rebuild and structure_cache.exists():
        with open(structure_cache, "rb") as fh:
            planned_pages = pickle.load(fh)
        if progress_cb:
            progress_cb(
                f"[Step 3/3] Loaded wiki structure from cache: {len(planned_pages)} pages.",
                45.0,
            )
    else:
        if progress_cb:
            progress_cb("[Step 3/3] Planning wiki structure (Phase 1)...", 41.0)
        planned_pages = plan_wiki_structure(agent, repo_path, project_name, comprehensive)
        with open(structure_cache, "wb") as fh:
            pickle.dump(planned_pages, fh)
        if progress_cb:
            progress_cb(
                f"[Step 3/3] Wiki structure planned: {len(planned_pages)} pages.",
                45.0,
            )

    high = [p for p in planned_pages if p["importance"] == "high"]
    others = [p for p in planned_pages if p["importance"] != "high"]
    pages_to_generate = (high + others)[:max_pages]
    total_pages = len(pages_to_generate)

    if progress_cb:
        progress_cb(
            f"[Step 3/3] Generating {total_pages} wiki pages "
            f"({'comprehensive' if comprehensive else 'concise'} mode)...",
            46.0,
        )

    wiki_dir = output_dir / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_name = os.getenv("MOONSHOT_MODEL", "kimi-k2.5")

    generated: list[dict] = []

    for i, page in enumerate(pages_to_generate, 1):
        try:
            content = generate_page_content(
                page, agent, repo_path, vector_store, embedder, func_map
            )
            mermaid_errors = validate_mermaid_blocks(content)
            if mermaid_errors:
                content, _ = fix_mermaid_errors(content, mermaid_errors, agent)

            page_file = wiki_dir / f"{page['id']}.md"
            page_file.write_text(content, encoding="utf-8")
            generated.append({**page, "content": content})

            # Map page progress to overall 46-98%
            page_pct = 46.0 + (i / total_pages) * 52.0
            if progress_cb:
                progress_cb(
                    f"[Step 3/3] Page {i}/{total_pages} done: {page['id']} — {page['title']} "
                    f"({len(content)} chars).",
                    page_pct,
                )
        except Exception as exc:
            err_content = f"# {page['title']}\n\n*生成失败: {exc}*"
            (wiki_dir / f"{page['id']}.md").write_text(err_content, encoding="utf-8")
            generated.append({**page, "content": err_content})
            page_pct = 46.0 + (i / total_pages) * 52.0
            if progress_cb:
                progress_cb(
                    f"[Step 3/3] Page {i}/{total_pages} FAILED: {page['id']} — {exc}",
                    page_pct,
                )

    # Write index.md
    total_funcs_row = builder.query("MATCH (f:Function) RETURN count(f) AS cnt")
    total_funcs = list(total_funcs_row[0].values())[0] if total_funcs_row else 0
    total_calls_row = builder.query("MATCH ()-[r:CALLS]->() RETURN count(r) AS cnt")
    total_calls = list(total_calls_row[0].values())[0] if total_calls_row else 0

    mode_label = "详细 Comprehensive" if comprehensive else "简洁 Concise"
    index_path = output_dir / "index.md"
    index_lines = [
        f"# {project_name} 源码 Wiki",
        "",
        f"*生成时间: {gen_time}*",
        f"*模型: {model_name} | 模式: {mode_label} | 上下文检索: 向量语义检索（Qwen3 Embedding）*",
        "",
        "---",
        "",
        "## 项目概览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 总函数数 | {total_funcs:,} |",
        f"| 总调用关系 | {total_calls:,} |",
        f"| 本次生成页面 | {len(generated)} |",
        "",
        "---",
        "",
        "## Wiki 页面索引",
        "",
        "| 重要性 | 页面 | 描述 |",
        "|--------|------|------|",
    ]
    for p in generated:
        importance_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p["importance"], "⚪")
        desc = p["description"]
        short_desc = desc[:60] + "..." if len(desc) > 60 else desc
        index_lines.append(
            f"| {importance_icon} {p['importance']} | [{p['title']}](./wiki/{p['id']}.md) | {short_desc} |"
        )
    index_lines += ["", "---", "", "## 详细文档", ""]
    for p in generated:
        index_lines.append(f"- [{p['title']}](./wiki/{p['id']}.md) — {p['description']}")

    index_path.write_text("\n".join(index_lines), encoding="utf-8")

    if progress_cb:
        progress_cb(
            f"[Step 3/3] Wiki complete: {len(generated)} pages at {output_dir}/",
            100.0,
        )

    return index_path, len(generated)


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

def save_meta(
    artifact_dir: Path,
    repo_path: Path,
    wiki_page_count: int,
    last_indexed_commit: str | None = None,
) -> None:
    """Save or update artifact metadata.

    Preserves existing fields (like step-completion flags) and updates
    the timestamp and wiki page count.
    """
    meta_file = artifact_dir / "meta.json"
    existing: dict = {}
    if meta_file.exists():
        try:
            existing = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass

    # Auto-detect which artifacts exist
    has_graph = (artifact_dir / "graph.db").exists()
    has_api_docs = (artifact_dir / "api_docs" / "index.md").exists()
    has_embeddings = (artifact_dir / "vectors.pkl").exists()
    has_wiki = wiki_page_count > 0 or (artifact_dir / "wiki" / "index.md").exists()

    meta = {
        **existing,
        "repo_path": repo_path.as_posix(),
        "repo_name": repo_path.name or "root",
        "indexed_at": datetime.now().isoformat(),
        "wiki_page_count": wiki_page_count,
        "steps": {
            "graph": has_graph,
            "api_docs": has_api_docs,
            "embeddings": has_embeddings,
            "wiki": has_wiki,
        },
        **({} if last_indexed_commit is None else {"last_indexed_commit": last_indexed_commit}),
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))


def artifact_dir_for(workspace: Path, repo_path: Path) -> Path:
    import hashlib

    # Use POSIX path for hashing so the same repo gets the same hash
    # regardless of OS (Windows backslash vs Unix forward slash).
    posix_path = repo_path.as_posix()
    h = hashlib.md5(posix_path.encode()).hexdigest()[:8]
    name = repo_path.name or repo_path.anchor.replace("\\", "").replace("/", "").replace(":", "") or "root"
    return workspace / f"{name}_{h}"
