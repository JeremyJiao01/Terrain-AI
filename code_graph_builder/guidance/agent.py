"""GuidanceAgent — ReAct-loop LLM agent that produces code generation guidance.

The agent receives a design document, uses tools to research the target
codebase, then synthesises a structured guidance Markdown file.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from ..rag.llm_backend import ChatMessage, LLMBackend, ToolCall
from .prompts import SYSTEM_PROMPT
from .toolset import ToolSet


class GuidanceAgent:
    """LLM agent with a tool-calling loop.

    Depends only on :class:`ToolSet` (abstract) and :class:`LLMBackend` —
    has no knowledge of concrete MCP services.
    """

    def __init__(
        self,
        toolset: ToolSet,
        llm: LLMBackend,
        max_iterations: int = 8,
        max_tokens: int = 8192,
    ) -> None:
        self._toolset = toolset
        self._llm = llm
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens

    async def run(self, design_doc: str) -> str:
        """Execute the ReAct loop and return the guidance Markdown."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": design_doc},
        ]

        tool_specs = self._toolset.tool_specs()

        for iteration in range(self._max_iterations):
            logger.debug(f"GuidanceAgent iteration {iteration + 1}/{self._max_iterations}")

            response = await asyncio.to_thread(
                self._llm.chat_with_tools,
                messages,
                tools=tool_specs or None,
                max_tokens=self._max_tokens,
            )

            if response.tool_calls:
                messages.append(self._assistant_msg(response))
                for tc in response.tool_calls:
                    result = await self._safe_call(tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                # No tool calls — final output
                return response.content or ""

        # Hit max iterations — force a final output without tools
        logger.warning(
            f"GuidanceAgent reached max iterations ({self._max_iterations}), "
            "forcing final output."
        )
        messages.append({
            "role": "user",
            "content": (
                "You have reached the maximum number of tool calls. "
                "Please produce the final guidance document now based on "
                "the information you have already gathered."
            ),
        })
        final = await asyncio.to_thread(
            self._llm.chat_with_tools,
            messages,
            tools=None,
            max_tokens=self._max_tokens,
        )
        return final.content or ""

    # -- Helpers -------------------------------------------------------------

    async def _safe_call(self, tc: ToolCall) -> str:
        """Execute a tool call, catching exceptions and returning them as text."""
        try:
            args = json.loads(tc.arguments)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {tc.arguments}"})

        logger.debug(f"Tool call: {tc.function_name}({args})")
        return await self._toolset.call(tc.function_name, args)

    @staticmethod
    def _assistant_msg(response: ChatMessage) -> dict[str, Any]:
        """Build the assistant message dict including tool_calls for the
        conversation history."""
        msg: dict[str, Any] = {"role": "assistant"}
        if response.content:
            msg["content"] = response.content
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function_name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in response.tool_calls
            ]
        return msg
