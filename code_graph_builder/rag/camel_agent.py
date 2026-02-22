"""CAMEL Agent wrapper for RAG integration.

This module provides integration with the CAMEL framework for multi-agent
code analysis workflows.

Note: This is a simplified implementation that provides CAMEL-like interfaces
without requiring the full CAMEL framework dependency. It can be extended
to use the actual CAMEL library if needed.

Examples:
    >>> from code_graph_builder.rag.camel_agent import CamelAgent
    >>> agent = CamelAgent(
    ...     role="Code Analyst",
    ...     goal="Analyze code and provide insights",
    ...     backstory="Expert in software architecture"
    ... )
    >>> result = agent.analyze("Explain this function", context="def foo(): pass")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from .kimi_client import KimiClient, create_kimi_client
from .prompt_templates import CodeAnalysisPrompts, CodeContext

if TYPE_CHECKING:
    from .rag_engine import RAGEngine, RAGResult


class AgentResponse(Protocol):
    """Protocol for agent responses."""

    content: str
    metadata: dict[str, Any]


@dataclass
class CamelAgentResponse:
    """Response from CAMEL agent.

    Attributes:
        content: Generated response content
        metadata: Additional metadata
        role: Agent role that generated the response
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    role: str = "agent"


class CamelAgent:
    """CAMEL-style agent for code analysis.

    Provides a CAMEL-like interface for single-agent code analysis tasks.
    This implementation uses Kimi k2.5 as the underlying model.

    Args:
        role: Agent's role (e.g., "Code Analyst")
        goal: Agent's goal/objective
        backstory: Agent's background/context
        kimi_client: Kimi API client
        verbose: Enable verbose logging

    Examples:
        >>> agent = CamelAgent(
        ...     role="Senior Python Developer",
        ...     goal="Review code for best practices",
        ...     backstory="10+ years of Python experience"
        ... )
        >>> response = agent.analyze("Review this function", code="def foo(): pass")
        >>> print(response.content)
    """

    def __init__(
        self,
        role: str,
        goal: str,
        backstory: str,
        kimi_client: KimiClient | None = None,
        verbose: bool = False,
    ):
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.kimi_client = kimi_client or create_kimi_client()
        self.verbose = verbose
        self.prompts = CodeAnalysisPrompts()

        # Build system prompt from role definition
        self.system_prompt = self._build_system_prompt()

        logger.info(f"Initialized CamelAgent: {role}")

    def _build_system_prompt(self) -> str:
        """Build system prompt from agent definition."""
        return f"""You are a {self.role}.

Your Goal: {self.goal}

Your Backstory: {self.backstory}

Guidelines:
1. Always stay in character as a {self.role}
2. Focus on achieving your stated goal
3. Use your expertise and background to provide insightful analysis
4. Be thorough but concise in your responses
5. When analyzing code, consider best practices, patterns, and potential issues

Respond in a professional, helpful manner."""

    def analyze(
        self,
        task: str,
        code: str | None = None,
        context: str | None = None,
    ) -> CamelAgentResponse:
        """Analyze code or answer a question.

        Args:
            task: Task description or question
            code: Code to analyze (optional)
            context: Additional context (optional)

        Returns:
            CamelAgentResponse with analysis
        """
        # Build user message
        user_content = task
        if code:
            user_content += f"\n\n```python\n{code}\n```"
        if context:
            user_content += f"\n\nContext: {context}"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            response = self.kimi_client.chat_with_messages(messages)
            return CamelAgentResponse(
                content=response.content,
                metadata={
                    "usage": response.usage,
                    "model": response.model,
                },
                role=self.role,
            )
        except Exception as e:
            logger.error(f"Agent analysis failed: {e}")
            return CamelAgentResponse(
                content=f"Error during analysis: {e}",
                metadata={"error": str(e)},
                role=self.role,
            )

    def review_code(
        self,
        code: str,
        review_type: str = "general",
    ) -> CamelAgentResponse:
        """Review code for specific aspects.

        Args:
            code: Code to review
            review_type: Type of review (general, security, performance, style)

        Returns:
            Code review response
        """
        review_prompts = {
            "general": "Please review this code for general quality, correctness, and best practices.",
            "security": "Please review this code for security vulnerabilities and best practices.",
            "performance": "Please review this code for performance issues and optimization opportunities.",
            "style": "Please review this code for code style, readability, and maintainability.",
        }

        prompt = review_prompts.get(review_type, review_prompts["general"])

        return self.analyze(
            task=f"{prompt}\n\nProvide specific recommendations with examples.",
            code=code,
        )

    def explain_code(
        self,
        code: str,
        detail_level: str = "medium",
    ) -> CamelAgentResponse:
        """Explain code in detail.

        Args:
            code: Code to explain
            detail_level: Level of detail (brief, medium, detailed)

        Returns:
            Code explanation
        """
        detail_instructions = {
            "brief": "Provide a brief, high-level summary of what this code does.",
            "medium": "Explain this code with a balance of high-level overview and key details.",
            "detailed": "Provide a detailed explanation covering all logic, edge cases, and design decisions.",
        }

        instruction = detail_instructions.get(detail_level, detail_instructions["medium"])

        return self.analyze(
            task=f"{instruction}\n\nFormat your response in markdown.",
            code=code,
        )

    def suggest_improvements(
        self,
        code: str,
        focus_areas: list[str] | None = None,
    ) -> CamelAgentResponse:
        """Suggest improvements for code.

        Args:
            code: Code to improve
            focus_areas: Specific areas to focus on (e.g., ["readability", "performance"])

        Returns:
            Improvement suggestions
        """
        task = "Suggest improvements for this code."

        if focus_areas:
            task += f"\n\nFocus on: {', '.join(focus_areas)}"

        task += "\n\nFor each suggestion, provide:\n1. The issue\n2. Why it matters\n3. A concrete improved example"

        return self.analyze(task=task, code=code)

    def answer_question(
        self,
        question: str,
        code_context: str | None = None,
    ) -> CamelAgentResponse:
        """Answer a question about code.

        Args:
            question: The question to answer
            code_context: Relevant code context

        Returns:
            Answer response
        """
        return self.analyze(
            task=question,
            code=code_context,
        )


class MultiAgentRAG:
    """Multi-agent RAG system using CAMEL-style agents.

    Coordinates multiple specialized agents for comprehensive code analysis.

    Args:
        rag_engine: RAG engine for retrieval
        verbose: Enable verbose logging

    Example:
        >>> multi_agent = MultiAgentRAG(rag_engine)
        >>> result = multi_agent.analyze(
        ...     query="Explain the authentication system",
        ...     analysis_types=["architecture", "security"]
        ... )
    """

    def __init__(
        self,
        rag_engine: RAGEngine,
        verbose: bool = False,
    ):
        self.rag_engine = rag_engine
        self.verbose = verbose

        # Initialize specialized agents
        self._init_agents()

    def _init_agents(self) -> None:
        """Initialize specialized agents."""
        self.architect = CamelAgent(
            role="Software Architect",
            goal="Analyze code architecture and design patterns",
            backstory="Senior architect with 15+ years of experience in system design",
            kimi_client=self.rag_engine.kimi_client,
            verbose=self.verbose,
        )

        self.security_expert = CamelAgent(
            role="Security Engineer",
            goal="Identify security vulnerabilities and best practices",
            backstory="Security specialist with expertise in secure coding practices",
            kimi_client=self.rag_engine.kimi_client,
            verbose=self.verbose,
        )

        self.performance_expert = CamelAgent(
            role="Performance Engineer",
            goal="Optimize code performance and resource usage",
            backstory="Performance optimization specialist with deep knowledge of algorithms",
            kimi_client=self.rag_engine.kimi_client,
            verbose=self.verbose,
        )

        self.documentation_writer = CamelAgent(
            role="Technical Writer",
            goal="Create clear, comprehensive documentation",
            backstory="Technical writer specializing in developer documentation",
            kimi_client=self.rag_engine.kimi_client,
            verbose=self.verbose,
        )

    def analyze(
        self,
        query: str,
        analysis_types: list[str] | None = None,
    ) -> dict[str, CamelAgentResponse]:
        """Run multi-agent analysis on a query.

        Args:
            query: User query
            analysis_types: Types of analysis to run (architecture, security, performance, docs)

        Returns:
            Dictionary of agent responses
        """
        if analysis_types is None:
            analysis_types = ["architecture", "docs"]

        # First, retrieve relevant code
        rag_result = self.rag_engine.query(query)

        # Build context from retrieved code
        context_parts = []
        for ctx in rag_result.contexts[:3]:  # Limit to top 3 contexts
            context_parts.append(ctx.format_context())
        code_context = "\n\n---\n\n".join(context_parts)

        # Run agent analyses
        results: dict[str, CamelAgentResponse] = {}

        if "architecture" in analysis_types:
            results["architecture"] = self.architect.analyze(
                task=f"Analyze the architecture and design patterns for: {query}",
                context=code_context,
            )

        if "security" in analysis_types:
            results["security"] = self.security_expert.analyze(
                task=f"Review security aspects of: {query}",
                context=code_context,
            )

        if "performance" in analysis_types:
            results["performance"] = self.performance_expert.analyze(
                task=f"Analyze performance characteristics of: {query}",
                context=code_context,
            )

        if "docs" in analysis_types:
            results["documentation"] = self.documentation_writer.analyze(
                task=f"Create documentation for: {query}",
                context=code_context,
            )

        return results

    def comprehensive_review(
        self,
        qualified_name: str,
    ) -> dict[str, CamelAgentResponse]:
        """Run comprehensive review of a code entity.

        Args:
            qualified_name: Fully qualified name of the entity

        Returns:
            Dictionary of agent reviews
        """
        # Get code explanation first
        rag_result = self.rag_engine.explain_code(qualified_name)

        # Get source code
        code = ""
        if rag_result.contexts:
            code = rag_result.contexts[0].source_code

        # Run all agents
        results: dict[str, CamelAgentResponse] = {
            "explanation": rag_result,
            "architecture": self.architect.analyze(
                task="Analyze the architecture and design patterns in this code",
                code=code,
            ),
            "security": self.security_expert.review_code(code, review_type="security"),
            "performance": self.performance_expert.review_code(code, review_type="performance"),
            "documentation": self.documentation_writer.explain_code(code, detail_level="detailed"),
        }

        return results


def create_camel_agent(
    role: str,
    goal: str,
    backstory: str,
    **kwargs: Any,
) -> CamelAgent:
    """Factory function to create a CAMEL agent.

    Args:
        role: Agent role
        goal: Agent goal
        backstory: Agent backstory
        **kwargs: Additional arguments for CamelAgent

    Returns:
        Configured CamelAgent
    """
    return CamelAgent(role=role, goal=goal, backstory=backstory, **kwargs)
