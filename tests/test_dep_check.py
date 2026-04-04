"""Tests for tools/dep_check.py — layer dependency checker."""

import os
import sys
import tempfile
import textwrap
import unittest

# Ensure the project root is on sys.path so we can import tools.dep_check
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.dep_check import (
    classify_layer,
    _get_domain,
    check_import,
    scan_file,
    main,
)


class TestClassifyLayer(unittest.TestCase):
    """Test classify_layer for all layer mappings and unknown paths."""

    def test_l0_types(self):
        self.assertEqual(
            classify_layer("code_graph_builder/foundation/types/models.py"), "L0"
        )

    def test_l0_types_nested(self):
        self.assertEqual(
            classify_layer("code_graph_builder/foundation/types/sub/foo.py"), "L0"
        )

    def test_l1_parsers(self):
        self.assertEqual(
            classify_layer("code_graph_builder/foundation/parsers/python.py"), "L1"
        )

    def test_l1_services(self):
        self.assertEqual(
            classify_layer("code_graph_builder/foundation/services/graph.py"), "L1"
        )

    def test_l1_utils(self):
        self.assertEqual(
            classify_layer("code_graph_builder/foundation/utils/helpers.py"), "L1"
        )

    def test_l2_core(self):
        self.assertEqual(
            classify_layer("code_graph_builder/domains/core/graph/builder.py"), "L2"
        )

    def test_l2_core_embedding(self):
        self.assertEqual(
            classify_layer("code_graph_builder/domains/core/embedding/store.py"), "L2"
        )

    def test_l3_upper(self):
        self.assertEqual(
            classify_layer("code_graph_builder/domains/upper/apidoc/gen.py"), "L3"
        )

    def test_l3_upper_rag(self):
        self.assertEqual(
            classify_layer("code_graph_builder/domains/upper/rag/search.py"), "L3"
        )

    def test_l4_entrypoints(self):
        self.assertEqual(
            classify_layer("code_graph_builder/entrypoints/cli.py"), "L4"
        )

    def test_l4_entrypoints_mcp(self):
        self.assertEqual(
            classify_layer("code_graph_builder/entrypoints/mcp/server.py"), "L4"
        )

    def test_unknown_flat_file(self):
        self.assertIsNone(classify_layer("code_graph_builder/builder.py"))

    def test_unknown_random_path(self):
        self.assertIsNone(classify_layer("some/random/path.py"))


class TestGetDomain(unittest.TestCase):
    """Test _get_domain extracts domain names correctly."""

    def test_core_graph(self):
        self.assertEqual(
            _get_domain("code_graph_builder/domains/core/graph/builder.py"), "graph"
        )

    def test_core_embedding(self):
        self.assertEqual(
            _get_domain("code_graph_builder/domains/core/embedding/store.py"),
            "embedding",
        )

    def test_upper_apidoc(self):
        self.assertEqual(
            _get_domain("code_graph_builder/domains/upper/apidoc/gen.py"), "apidoc"
        )

    def test_upper_rag(self):
        self.assertEqual(
            _get_domain("code_graph_builder/domains/upper/rag/search.py"), "rag"
        )

    def test_entrypoints_cli(self):
        self.assertEqual(
            _get_domain("code_graph_builder/entrypoints/cli.py"), "cli"
        )

    def test_entrypoints_mcp(self):
        self.assertEqual(
            _get_domain("code_graph_builder/entrypoints/mcp/server.py"), "mcp"
        )

    def test_no_domain(self):
        self.assertIsNone(
            _get_domain("code_graph_builder/foundation/types/models.py")
        )


class TestCheckImport(unittest.TestCase):
    """Test check_import for allowed/forbidden imports at each layer."""

    # --- stdlib and third-party are always allowed ---

    def test_stdlib_allowed_everywhere(self):
        for path in [
            "code_graph_builder/foundation/types/models.py",
            "code_graph_builder/foundation/parsers/python.py",
            "code_graph_builder/domains/core/graph/builder.py",
            "code_graph_builder/domains/upper/apidoc/gen.py",
            "code_graph_builder/entrypoints/cli.py",
        ]:
            self.assertIsNone(check_import(path, "os"))
            self.assertIsNone(check_import(path, "json"))

    def test_thirdparty_allowed_everywhere(self):
        for path in [
            "code_graph_builder/foundation/types/models.py",
            "code_graph_builder/foundation/parsers/python.py",
            "code_graph_builder/domains/core/graph/builder.py",
            "code_graph_builder/domains/upper/apidoc/gen.py",
            "code_graph_builder/entrypoints/cli.py",
        ]:
            self.assertIsNone(check_import(path, "pydantic"))

    # --- L0: no project imports ---

    def test_l0_cannot_import_project(self):
        result = check_import(
            "code_graph_builder/foundation/types/models.py",
            "code_graph_builder.foundation.parsers.python",
        )
        self.assertIsNotNone(result)

    def test_l0_cannot_import_l0(self):
        # L0 can't even import other L0 modules via project path
        result = check_import(
            "code_graph_builder/foundation/types/models.py",
            "code_graph_builder.foundation.types.other",
        )
        self.assertIsNotNone(result)

    # --- L1: can import L0 ---

    def test_l1_can_import_l0(self):
        result = check_import(
            "code_graph_builder/foundation/parsers/python.py",
            "code_graph_builder.foundation.types.models",
        )
        self.assertIsNone(result)

    def test_l1_cannot_import_l2(self):
        result = check_import(
            "code_graph_builder/foundation/parsers/python.py",
            "code_graph_builder.domains.core.graph.builder",
        )
        self.assertIsNotNone(result)

    def test_l1_cannot_import_l1_cross_subdirectory(self):
        # L1 parsers cannot import L1 services
        result = check_import(
            "code_graph_builder/foundation/parsers/python.py",
            "code_graph_builder.foundation.services.graph",
        )
        self.assertIsNotNone(result)

    def test_l1_cannot_import_l1_same_subdirectory(self):
        # L1 parsers cannot import another L1 parsers module either
        result = check_import(
            "code_graph_builder/foundation/parsers/factory.py",
            "code_graph_builder.foundation.parsers.utils",
        )
        self.assertIsNotNone(result)

    # --- L2: can import L0, L1; no cross-domain ---

    def test_l2_can_import_l0(self):
        result = check_import(
            "code_graph_builder/domains/core/graph/builder.py",
            "code_graph_builder.foundation.types.models",
        )
        self.assertIsNone(result)

    def test_l2_can_import_l1(self):
        result = check_import(
            "code_graph_builder/domains/core/graph/builder.py",
            "code_graph_builder.foundation.parsers.python",
        )
        self.assertIsNone(result)

    def test_l2_cannot_import_l3(self):
        result = check_import(
            "code_graph_builder/domains/core/graph/builder.py",
            "code_graph_builder.domains.upper.apidoc.gen",
        )
        self.assertIsNotNone(result)

    def test_l2_cross_domain_forbidden(self):
        # graph cannot import embedding
        result = check_import(
            "code_graph_builder/domains/core/graph/builder.py",
            "code_graph_builder.domains.core.embedding.store",
        )
        self.assertIsNotNone(result)

    # --- L3: can import L0, L1, L2; no cross-domain ---

    def test_l3_can_import_l0(self):
        result = check_import(
            "code_graph_builder/domains/upper/apidoc/gen.py",
            "code_graph_builder.foundation.types.models",
        )
        self.assertIsNone(result)

    def test_l3_can_import_l2(self):
        result = check_import(
            "code_graph_builder/domains/upper/apidoc/gen.py",
            "code_graph_builder.domains.core.graph.builder",
        )
        self.assertIsNone(result)

    def test_l3_cannot_import_l4(self):
        result = check_import(
            "code_graph_builder/domains/upper/apidoc/gen.py",
            "code_graph_builder.entrypoints.cli",
        )
        self.assertIsNotNone(result)

    def test_l3_cross_domain_forbidden(self):
        # apidoc cannot import rag
        result = check_import(
            "code_graph_builder/domains/upper/apidoc/gen.py",
            "code_graph_builder.domains.upper.rag.search",
        )
        self.assertIsNotNone(result)

    # --- L4: can import L0-L3; no cross-entrypoint ---

    def test_l4_can_import_l2(self):
        result = check_import(
            "code_graph_builder/entrypoints/cli.py",
            "code_graph_builder.domains.core.graph.builder",
        )
        self.assertIsNone(result)

    def test_l4_can_import_l3(self):
        result = check_import(
            "code_graph_builder/entrypoints/cli.py",
            "code_graph_builder.domains.upper.apidoc.gen",
        )
        self.assertIsNone(result)

    def test_l4_cross_entrypoint_forbidden(self):
        # mcp cannot import cli
        result = check_import(
            "code_graph_builder/entrypoints/mcp/server.py",
            "code_graph_builder.entrypoints.cli",
        )
        self.assertIsNotNone(result)


class TestScanFile(unittest.TestCase):
    """Test scan_file with temp files containing violations vs clean code."""

    def _write_temp(self, code: str, suffix: str = ".py") -> str:
        """Write code to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "w") as f:
            f.write(textwrap.dedent(code))
        return path

    def test_clean_l1_file(self):
        code = """\
        import os
        from code_graph_builder.foundation.types.models import Node
        """
        path = self._write_temp(code)
        try:
            violations = scan_file(
                path,
                file_layer_path="code_graph_builder/foundation/parsers/python.py",
            )
            self.assertEqual(violations, [])
        finally:
            os.unlink(path)

    def test_violation_l0_imports_project(self):
        code = """\
        from code_graph_builder.foundation.parsers.python import parse
        """
        path = self._write_temp(code)
        try:
            violations = scan_file(
                path,
                file_layer_path="code_graph_builder/foundation/types/models.py",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("L0", violations[0])
        finally:
            os.unlink(path)

    def test_violation_cross_domain(self):
        code = """\
        from code_graph_builder.domains.core.embedding.store import VectorStore
        """
        path = self._write_temp(code)
        try:
            violations = scan_file(
                path,
                file_layer_path="code_graph_builder/domains/core/graph/builder.py",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("cross-domain", violations[0].lower())
        finally:
            os.unlink(path)

    def test_stdlib_only_no_violations(self):
        code = """\
        import os
        import sys
        import json
        from pathlib import Path
        """
        path = self._write_temp(code)
        try:
            violations = scan_file(
                path,
                file_layer_path="code_graph_builder/foundation/types/models.py",
            )
            self.assertEqual(violations, [])
        finally:
            os.unlink(path)

    def test_multiple_violations(self):
        code = """\
        from code_graph_builder.foundation.parsers.python import parse
        from code_graph_builder.domains.core.graph.builder import Builder
        """
        path = self._write_temp(code)
        try:
            violations = scan_file(
                path,
                file_layer_path="code_graph_builder/foundation/types/models.py",
            )
            self.assertEqual(len(violations), 2)
        finally:
            os.unlink(path)


class TestMain(unittest.TestCase):
    """Test main entry point."""

    def test_main_on_empty_dir(self):
        """An empty directory should produce zero violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(tmpdir)
            self.assertEqual(result, 0)

    def test_main_skips_tests_dir(self):
        """Files in tests/ should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tests_dir = os.path.join(
                tmpdir,
                "code_graph_builder",
                "tests",
            )
            os.makedirs(tests_dir)
            with open(os.path.join(tests_dir, "test_foo.py"), "w") as f:
                f.write("from code_graph_builder.entrypoints.cli import main\n")
            result = main(tmpdir)
            self.assertEqual(result, 0)

    def test_main_skips_examples_dir(self):
        """Files in examples/ should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            examples_dir = os.path.join(
                tmpdir,
                "code_graph_builder",
                "examples",
            )
            os.makedirs(examples_dir)
            with open(os.path.join(examples_dir, "demo.py"), "w") as f:
                f.write("from code_graph_builder.entrypoints.cli import main\n")
            result = main(tmpdir)
            self.assertEqual(result, 0)

    def test_main_detects_violation(self):
        """A file with violations should cause non-zero exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_dir = os.path.join(
                tmpdir,
                "code_graph_builder",
                "foundation",
                "types",
            )
            os.makedirs(file_dir)
            with open(os.path.join(file_dir, "models.py"), "w") as f:
                f.write(
                    "from code_graph_builder.foundation.parsers.python import parse\n"
                )
            result = main(tmpdir)
            self.assertNotEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
