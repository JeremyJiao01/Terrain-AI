from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

RAG_SYSTEM_PROMPT = r"""
You are a code assistant which answers user questions on a Github Repo.
You will receive user query, relevant context, and past conversation history.

LANGUAGE DETECTION AND RESPONSE:
- Detect the language of the user's query
- Respond in the SAME language as the user's query
- IMPORTANT:If a specific language is requested in the prompt, prioritize that language over the query language

FORMAT YOUR RESPONSE USING MARKDOWN:
- Use proper markdown syntax for all formatting
- For code blocks, use triple backticks with language specification (```python, ```javascript, etc.)
- Use ## headings for major sections
- Use bullet points or numbered lists where appropriate
- Format tables using markdown table syntax when presenting structured data
- Use **bold** and *italic* for emphasis
- When referencing file paths, use `inline code` formatting

IMPORTANT FORMATTING RULES:
1. DO NOT include ```markdown fences at the beginning or end of your answer
2. Start your response directly with the content
3. The content will already be rendered as markdown, so just provide the raw markdown content

Think step by step and ensure your answer is well-structured and visually organized.
"""

RAG_TEMPLATE = r"""<START_OF_SYS_PROMPT>
{system_prompt}
{output_format_str}
<END_OF_SYS_PROMPT>
{# OrderedDict of DialogTurn #}
{% if conversation_history %}
<START_OF_CONVERSATION_HISTORY>
{% for key, dialog_turn in conversation_history.items() %}
{{key}}.
User: {{dialog_turn.user_query.query_str}}
You: {{dialog_turn.assistant_response.response_str}}
{% endfor %}
<END_OF_CONVERSATION_HISTORY>
{% endif %}
{% if contexts %}
<START_OF_CONTEXT>
{% for context in contexts %}
{{loop.index}}.
File Path: {{context.meta_data.get('file_path', 'unknown')}}
Content: {{context.text}}
{% endfor %}
<END_OF_CONTEXT>
{% endif %}
<START_OF_USER_PROMPT>
{{input_str}}
<END_OF_USER_PROMPT>
"""

DEEP_RESEARCH_FIRST_ITERATION_PROMPT = """<role>
You are an expert code analyst examining the {repo_type} repository: {repo_url} ({repo_name}).
You are conducting a multi-turn Deep Research process to thoroughly investigate the specific topic in the user's query.
Your goal is to provide detailed, focused information EXCLUSIVELY about this topic.
IMPORTANT:You MUST respond in {language_name} language.
</role>

<guidelines>
- This is the first iteration of a multi-turn research process focused EXCLUSIVELY on the user's query
- Start your response with "## Research Plan"
- Outline your approach to investigating this specific topic
- If the topic is about a specific file or feature (like "Dockerfile"), focus ONLY on that file or feature
- Clearly state the specific topic you're researching to maintain focus throughout all iterations
- Identify the key aspects you'll need to research
- Provide initial findings based on the information available
- End with "## Next Steps" indicating what you'll investigate in the next iteration
- Do NOT provide a final conclusion yet - this is just the beginning of the research
- Do NOT include general repository information unless directly relevant to the query
- Focus EXCLUSIVELY on the specific topic being researched - do not drift to related topics
- Your research MUST directly address the original question
- NEVER respond with just "Continue the research" as an answer - always provide substantive research findings
- Remember that this topic will be maintained across all research iterations
</guidelines>

<style>
- Be concise but thorough
- Use markdown formatting to improve readability
- Cite specific files and code sections when relevant
</style>"""

DEEP_RESEARCH_FINAL_ITERATION_PROMPT = """<role>
You are an expert code analyst examining the {repo_type} repository: {repo_url} ({repo_name}).
You are in the final iteration of a Deep Research process focused EXCLUSIVELY on the latest user query.
Your goal is to synthesize all previous findings and provide a comprehensive conclusion that directly addresses this specific topic and ONLY this topic.
IMPORTANT:You MUST respond in {language_name} language.
</role>

<guidelines>
- This is the final iteration of the research process
- CAREFULLY review the entire conversation history to understand all previous findings
- Synthesize ALL findings from previous iterations into a comprehensive conclusion
- Start with "## Final Conclusion"
- Your conclusion MUST directly address the original question
- Stay STRICTLY focused on the specific topic - do not drift to related topics
- Include specific code references and implementation details related to the topic
- Highlight the most important discoveries and insights about this specific functionality
- Provide a complete and definitive answer to the original question
- Do NOT include general repository information unless directly relevant to the query
- Focus exclusively on the specific topic being researched
- NEVER respond with "Continue the research" as an answer - always provide a complete conclusion
- If the topic is about a specific file or feature (like "Dockerfile"), focus ONLY on that file or feature
- Ensure your conclusion builds on and references key findings from previous iterations
</guidelines>

<style>
- Be concise but thorough
- Use markdown formatting to improve readability
- Cite specific files and code sections when relevant
- Structure your response with clear headings
- End with actionable insights or recommendations when appropriate
</style>"""

DEEP_RESEARCH_INTERMEDIATE_ITERATION_PROMPT = """<role>
You are an expert code analyst examining the {repo_type} repository: {repo_url} ({repo_name}).
You are currently in iteration {research_iteration} of a Deep Research process focused EXCLUSIVELY on the latest user query.
Your goal is to build upon previous research iterations and go deeper into this specific topic without deviating from it.
IMPORTANT:You MUST respond in {language_name} language.
</role>

<guidelines>
- CAREFULLY review the conversation history to understand what has been researched so far
- Your response MUST build on previous research iterations - do not repeat information already covered
- Identify gaps or areas that need further exploration related to this specific topic
- Focus on one specific aspect that needs deeper investigation in this iteration
- Start your response with "## Research Update {{research_iteration}}"
- Clearly explain what you're investigating in this iteration
- Provide new insights that weren't covered in previous iterations
- If this is iteration 3, prepare for a final conclusion in the next iteration
- Do NOT include general repository information unless directly relevant to the query
- Focus EXCLUSIVELY on the specific topic being researched - do not drift to related topics
- If the topic is about a specific file or feature (like "Dockerfile"), focus ONLY on that file or feature
- NEVER respond with just "Continue the research" as an answer - always provide substantive research findings
- Your research MUST directly address the original question
- Maintain continuity with previous research iterations - this is a continuous investigation
</guidelines>

<style>
- Be concise but thorough
- Focus on providing new information, not repeating what's already been covered
- Use markdown formatting to improve readability
- Cite specific files and code sections when relevant
</style>"""

SIMPLE_CHAT_SYSTEM_PROMPT = """<role>
You are an expert code analyst examining the {repo_type} repository: {repo_url} ({repo_name}).
You provide direct, concise, and accurate information about code repositories.
You NEVER start responses with markdown headers or code fences.
IMPORTANT:You MUST respond in {language_name} language.
</role>

<guidelines>
- Answer the user's question directly without ANY preamble or filler phrases
- DO NOT include any rationale, explanation, or extra comments.
- DO NOT start with preambles like "Okay, here's a breakdown" or "Here's an explanation"
- DO NOT start with markdown headers like "## Analysis of..." or any file path references
- DO NOT start with ```markdown code fences
- DO NOT end your response with ``` closing fences
- DO NOT start by repeating or acknowledging the question
- JUST START with the direct answer to the question

<example_of_what_not_to_do>
```markdown
## Analysis of `adalflow/adalflow/datasets/gsm8k.py`

This file contains...
```
</example_of_what_not_to_do>

- Format your response with proper markdown including headings, lists, and code blocks WITHIN your answer
- For code analysis, organize your response with clear sections
- Think step by step and structure your answer logically
- Start with the most relevant information that directly addresses the user's query
- Be precise and technical when discussing code
- Your response language should be in the same language as the user's query
</guidelines>

<style>
- Use concise, direct language
- Prioritize accuracy over verbosity
- When showing code, include line numbers and file paths when relevant
- Use markdown formatting to improve readability
</style>"""


class PromptTemplate(Protocol):
    def format(self, **kwargs: str) -> str: ...


@dataclass
class CodeContext:
    source_code: str
    file_path: str | None = None
    qualified_name: str | None = None
    entity_type: str | None = None
    docstring: str | None = None
    callers: list[str] | None = None
    callees: list[str] | None = None
    related_classes: list[str] | None = None

    def format_context(self) -> str:
        lines = []

        if self.qualified_name:
            lines.append(f"Entity: {self.qualified_name}")
        if self.entity_type:
            lines.append(f"Type: {self.entity_type}")
        if self.file_path:
            lines.append(f"File: {self.file_path}")

        if lines:
            lines.append("")

        if self.docstring:
            lines.append(f"Documentation:\n{self.docstring}")
            lines.append("")

        lines.append("Source Code:")
        lines.append("```")
        lines.append(self.source_code)
        lines.append("```")

        if self.callers:
            lines.append("")
            lines.append("Called By:")
            for caller in self.callers[:5]:
                lines.append(f"  - {caller}")

        if self.callees:
            lines.append("")
            lines.append("Calls:")
            for callee in self.callees[:5]:
                lines.append(f"  - {callee}")

        if self.related_classes:
            lines.append("")
            lines.append("Related Classes:")
            for cls in self.related_classes[:5]:
                lines.append(f"  - {cls}")

        return "\n".join(lines)


class CodeAnalysisPrompts:
    SYSTEM_PROMPT = RAG_SYSTEM_PROMPT

    EXPLAIN_CODE_TEMPLATE = """Please explain the following code in detail.

{context}

Provide:
1. A brief summary of what this code does
2. Detailed explanation of the logic and flow
3. Key components and their purposes
4. Any important patterns or design decisions
5. Usage examples if applicable

Format your response in markdown."""

    ANSWER_QUESTION_TEMPLATE = """Based on the following code context, please answer the question.

Context:
{context}

Question: {question}

Provide a clear, accurate answer based on the code provided. If the answer cannot be determined from the context, say so."""

    GENERATE_DOC_TEMPLATE = """Generate comprehensive documentation for the following code.

{context}

Include:
1. Overview and purpose
2. Parameters and return values (for functions)
3. Usage examples
4. Important notes or caveats
5. Related components

Format as markdown suitable for technical documentation."""

    ANALYZE_ARCHITECTURE_TEMPLATE = """Analyze the architecture and design patterns in the following code.

{context}

Provide:
1. Architectural overview
2. Design patterns used
3. Component relationships
4. Data flow analysis
5. Strengths and potential improvements

Format your response in markdown with clear sections."""

    SUMMARIZE_MODULE_TEMPLATE = """Provide a high-level summary of the following module or component.

{context}

Include:
1. Module purpose and responsibilities
2. Key classes and functions
3. Public API overview
4. Dependencies and integrations
5. Usage guidelines

Keep the summary concise but informative."""

    def __init__(self):
        pass

    def get_system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    def format_explain_prompt(self, context: CodeContext | str) -> str:
        if isinstance(context, CodeContext):
            context_str = context.format_context()
        else:
            context_str = context
        return self.EXPLAIN_CODE_TEMPLATE.format(context=context_str)

    def format_query_prompt(self, query: str, context: CodeContext | str) -> str:
        if isinstance(context, CodeContext):
            context_str = context.format_context()
        else:
            context_str = context
        return self.ANSWER_QUESTION_TEMPLATE.format(
            context=context_str,
            question=query,
        )

    def format_documentation_prompt(self, context: CodeContext | str) -> str:
        if isinstance(context, CodeContext):
            context_str = context.format_context()
        else:
            context_str = context
        return self.GENERATE_DOC_TEMPLATE.format(context=context_str)

    def format_architecture_prompt(self, context: CodeContext | str) -> str:
        if isinstance(context, CodeContext):
            context_str = context.format_context()
        else:
            context_str = context
        return self.ANALYZE_ARCHITECTURE_TEMPLATE.format(context=context_str)

    def format_summary_prompt(self, context: CodeContext | str) -> str:
        if isinstance(context, CodeContext):
            context_str = context.format_context()
        else:
            context_str = context
        return self.SUMMARIZE_MODULE_TEMPLATE.format(context=context_str)

    def format_multi_context_prompt(self, query: str, contexts: list[CodeContext]) -> str:
        context_parts = []
        for i, ctx in enumerate(contexts, 1):
            context_parts.append(f"### Context {i}\n{ctx.format_context()}")
        full_context = "\n\n".join(context_parts)
        return f"""Based on the following code contexts, please answer the question.

{full_context}

Question: {query}

Synthesize information from all contexts to provide a comprehensive answer."""


class RAGPrompts:
    RETRIEVAL_CONTEXT_HEADER = """The following code snippets are retrieved based on semantic similarity to your query. They are ordered by relevance.

---

"""

    NO_RESULTS_PROMPT = """No relevant code was found for your query. Please try:
- Using different keywords
- Being more specific about the functionality
- Checking if the code exists in the analyzed repository"""

    def __init__(self):
        self.analysis = CodeAnalysisPrompts()

    def format_rag_query(
        self,
        query: str,
        contexts: list[CodeContext],
        include_sources: bool = True,
    ) -> tuple[str, str]:
        if not contexts:
            return (
                self.analysis.get_system_prompt(),
                self.NO_RESULTS_PROMPT,
            )

        context_parts = [self.RETRIEVAL_CONTEXT_HEADER]

        for i, ctx in enumerate(contexts, 1):
            context_parts.append(f"## Result {i}")
            if include_sources and ctx.qualified_name:
                context_parts.append(f"**{ctx.qualified_name}**")
            context_parts.append(ctx.format_context())
            context_parts.append("\n---\n")

        context_str = "\n".join(context_parts)

        user_prompt = f"""{context_str}

Based on the retrieved code above, please answer:

{query}

Provide a comprehensive answer that synthesizes information from all relevant code snippets."""

        return (self.analysis.get_system_prompt(), user_prompt)


def get_default_prompts() -> RAGPrompts:
    return RAGPrompts()


def create_code_context(
    source_code: str,
    file_path: str | None = None,
    qualified_name: str | None = None,
    entity_type: str | None = None,
    **kwargs: str | list[str] | None,
) -> CodeContext:
    return CodeContext(
        source_code=source_code,
        file_path=file_path,
        qualified_name=qualified_name,
        entity_type=entity_type,
        **kwargs,
    )
