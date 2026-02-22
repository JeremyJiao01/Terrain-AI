"""Code Graph Builder - Call Resolver."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from .. import constants as cs
from ..types import FunctionRegistryTrieProtocol

if TYPE_CHECKING:
    from .import_processor import ImportProcessor


class CallResolver:
    """Resolve function calls to their targets."""

    def __init__(
        self,
        function_registry: FunctionRegistryTrieProtocol,
        import_processor: ImportProcessor,
    ) -> None:
        self.function_registry = function_registry
        self.import_processor = import_processor

    def resolve_call(
        self,
        call_name: str,
        module_qn: str,
        class_context: str | None = None,
    ) -> str | None:
        """
        Resolve a function call to its fully qualified name.

        Args:
            call_name: The name of the call (e.g., "foo", "module.bar", "self.method")
            module_qn: The qualified name of the current module
            class_context: The class context if inside a class method

        Returns:
            The fully qualified name of the target function, or None if not resolved
        """
        if not call_name:
            return None

        # Try to resolve self/this calls within class context
        if class_context and self._is_self_call(call_name):
            return self._resolve_self_call(call_name, class_context)

        # Try direct resolution (fully qualified name)
        if cs.SEPARATOR_DOT in call_name:
            return self._resolve_qualified_call(call_name, module_qn)

        # Try import resolution
        if resolved := self._resolve_via_imports(call_name, module_qn):
            return resolved

        # Try same module resolution
        if resolved := self._resolve_same_module(call_name, module_qn):
            return resolved

        # Try function registry lookup
        return self._resolve_via_registry(call_name, module_qn)

    def _is_self_call(self, call_name: str) -> bool:
        """Check if this is a self/this call."""
        return call_name.startswith("self.") or call_name.startswith("this.")

    def _resolve_self_call(self, call_name: str, class_context: str) -> str | None:
        """Resolve a self/this call to a method."""
        # Remove self./this. prefix
        if call_name.startswith("self."):
            method_name = call_name[5:]
        elif call_name.startswith("this."):
            method_name = call_name[5:]
        else:
            method_name = call_name

        # Try to find method in class
        method_qn = f"{class_context}.{method_name}"
        if method_qn in self.function_registry:
            return method_qn

        return None

    def _resolve_qualified_call(self, call_name: str, module_qn: str) -> str | None:
        """Resolve a qualified call like 'module.function'."""
        parts = call_name.split(cs.SEPARATOR_DOT)

        if len(parts) >= 2:
            # Check if first part is an imported module
            import_map = self.import_processor.get_import_mapping(module_qn)

            if parts[0] in import_map:
                imported = import_map[parts[0]]
                # Reconstruct with imported module
                rest = cs.SEPARATOR_DOT.join(parts[1:])
                full_qn = f"{imported}.{rest}"

                if full_qn in self.function_registry:
                    return full_qn

        # Try as fully qualified
        if call_name in self.function_registry:
            return call_name

        return None

    def _resolve_via_imports(self, call_name: str, module_qn: str) -> str | None:
        """Try to resolve call through import mapping."""
        import_map = self.import_processor.get_import_mapping(module_qn)

        if call_name in import_map:
            imported_qn = import_map[call_name]
            if imported_qn in self.function_registry:
                return imported_qn

        return None

    def _resolve_same_module(self, call_name: str, module_qn: str) -> str | None:
        """Try to resolve call in the same module."""
        full_qn = f"{module_qn}.{call_name}"

        if full_qn in self.function_registry:
            return full_qn

        return None

    def _resolve_via_registry(self, call_name: str, module_qn: str) -> str | None:
        """Try to find function in registry by simple name."""
        # This is a fallback that might return incorrect results
        # if multiple functions have the same name
        for qn in self.function_registry._entries.keys() if hasattr(self.function_registry, '_entries') else []:
            if qn.endswith(f".{call_name}"):
                logger.debug(f"Resolved {call_name} to {qn} via registry lookup")
                return qn

        return None
