# Code-Gen Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a `.claude/commands/code-gen.md` skill file that guides external Agents through a 4-stage MCP tool workflow, producing a structured implementation plan from a design document.

**Architecture:** Single skill file referencing existing MCP tools (`find_api`, `get_api_doc`, `find_callers`, `trace_call_chain`, `list_api_docs`, `get_repository_info`). No new MCP tools or Python code needed — the skill is pure Agent-side instruction.

**Tech Stack:** Markdown (Claude Code custom command format)

---

### Task 1: Create the code-gen skill file

**Files:**
- Create: `.claude/commands/code-gen.md`

- [ ] **Step 1: Create the skill file**

Create `.claude/commands/code-gen.md` with the complete 4-stage workflow. The file uses the same format as other `.claude/commands/*.md` files (plain markdown, no YAML frontmatter — the existing skills like `api-find.md`, `cgb-start.md` don't use frontmatter).

The skill content must cover:

1. **阶段 0 — 环境检查**: Call `get_repository_info` MCP tool to verify active repo with graph + api_docs + embeddings
2. **阶段 1 — 概念提取**: Parse design doc, extract 2-8 key concepts (no tool calls)
3. **阶段 2 — 广度搜索**: Call `find_api` for each concept
4. **阶段 3 — 深度调研**: Call `get_api_doc` (mandatory per candidate), `find_callers`, `trace_call_chain`, `list_api_docs` as needed
5. **阶段 3.5 — 补漏检查**: Review phase 3 results for undiscovered interfaces, loop back once if needed
6. **阶段 4 — 方案输出**: Output structured implementation plan in fixed format, STOP and wait for user confirmation

```markdown
Generate a structured implementation plan for a feature described in the design document below, by researching the indexed codebase through MCP tools.

**Input:** $ARGUMENTS (path to design document, or inline design text)

---

## Pre-flight

Call `get_repository_info` to verify the active repository has all services available:
- graph: true
- api_docs: true
- embeddings: true

If any service is missing, stop and say:
> "Repository index is incomplete. Please run `/repo-init <repo-path>` first."

If the input is a file path, read the file. Otherwise treat $ARGUMENTS as the design text.

---

## Phase 1: Concept Extraction (no tool calls)

Read the design document and extract:

- **Functional concepts** — capability keywords (e.g. "serial init", "fault registration", "timer callback")
- **Entity names** — specific module/function/type names mentioned
- **Action verbs** — init, register, callback, poll, etc. — these hint at which interface patterns to search

Produce 2-8 concepts. Each concept is a search keyword for Phase 2.

---

## Phase 2: Broad Search

For each concept from Phase 1, call:

```
find_api(query="<concept keyword>", top_k=5)
```

From the results:
- Keep semantically relevant matches (high score + your judgment)
- Deduplicate (same qualified_name from multiple searches)
- Note which design-doc concept each candidate relates to

If a concept yields no results, rephrase the keyword and retry once. If still nothing, mark it as "no existing implementation found".

---

## Phase 3: Deep Research

For each candidate interface from Phase 2:

| Action | MCP Tool | When |
|--------|----------|------|
| Get full signature, call tree, source code | `get_api_doc(qualified_name="...")` | **Every candidate — mandatory** |
| Find who calls this interface | `find_callers(function_name="...")` | When you need to understand usage patterns |
| Trace full call chain to entry points | `trace_call_chain(target_function="...")` | When you need to confirm scope of impact |
| Browse module hierarchy | `list_api_docs()` or `list_api_docs(module="...")` | **At least once** — to determine where new functions should be placed |

Extract from the results:

1. **Reusable interfaces** — confirmed signatures, parameter semantics, preconditions
2. **Usage patterns** — from `find_callers` results, observe how other code calls each interface (parameter passing, error handling)
3. **Code style** — from `get_api_doc` source code, note naming conventions, comment language, error handling patterns
4. **Dependency direction** — from call trees, confirm that new code calling existing interfaces respects the dependency direction (no reverse dependencies)

---

## Phase 3.5: Gap Check

Review Phase 3 results and check:

- Are there qualified_names in call trees (callees) that the design document references but Phase 2 did not search?
- Are there callers that suggest a dependency the design document missed?

**Gap criteria** — an interface counts as a gap if:
1. It is mentioned (directly or indirectly) in the design document but was not found in Phase 2, OR
2. It is a direct callee of a candidate interface that the new code will likely need to call directly

If gaps found → run Phase 2 + Phase 3 for the new interfaces (**one round only**)
If no gaps → proceed to Phase 4

---

## Phase 4: Output

Synthesize all research into this exact format:

```
# Implementation Plan

## Goal
[One paragraph summary from the design document]

## Existing Interfaces to Reuse
| Interface | Signature | Location | Usage Notes |
|-----------|-----------|----------|-------------|
| `qualified_name` | `return_type func(params)` | `file:line` | How to call, preconditions, caveats |

## New Functions to Create
| Function | Module/File | Responsibility | Dependencies |
|----------|-------------|----------------|--------------|
| `new_func` | `path` | What it does | Which existing interfaces it calls |

## Files to Modify
| File | Change | Reason |
|------|--------|--------|
| `path` | What to change | Why |

## Dependency Order
file_a → file_b → file_c

## Code Style Conventions
- Naming: ...
- Error handling: ...
- Comment language: ...

## Architecture Constraints
- Dependency direction: ...
- Layer placement: ...
```

**⚠️ STOP HERE.** Present this plan to the user and wait for explicit confirmation before taking any further action. Do not write code until the user approves.
```

- [ ] **Step 2: Verify the skill file loads correctly**

Run:
```bash
cat .claude/commands/code-gen.md | head -5
```

Expected: First line should be "Generate a structured implementation plan..."

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/code-gen.md
git commit -m "feat: add /code-gen skill — 4-stage MCP workflow for implementation planning"
```

---

### Task 2: Verify skill is discoverable

- [ ] **Step 1: Check that Claude Code discovers the skill**

In a Claude Code session in this project, type `/code-gen` and verify it appears in the autocomplete list. The skill content should load when selected.

- [ ] **Step 2: Smoke test with a simple design doc**

Create a minimal test design document and invoke `/code-gen` to verify:
1. Phase 0 calls `get_repository_info` and checks services
2. The Agent follows the 4-stage flow
3. Output matches the expected format
4. Agent stops and waits for confirmation at Phase 4

This is a manual verification step — no automated test needed since the skill is a prompt, not code.

---

### Task 3: Update project documentation

**Files:**
- Modify: `CLAUDE.md` (add reference to `/code-gen`)

- [ ] **Step 1: Add code-gen to CLAUDE.md Key Entry Points**

Add after the existing Key Entry Points section:

```markdown
## Custom Commands

- `/code-gen <design-doc>`: Generate implementation plan from design document using MCP tools
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add /code-gen to CLAUDE.md key commands"
```
