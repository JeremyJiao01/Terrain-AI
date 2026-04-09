"""Code Graph Builder - Constants."""

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of StrEnum for Python 3.10."""
from typing import NamedTuple


class UniqueKeyType(StrEnum):
    NAME = "name"
    PATH = "path"
    QUALIFIED_NAME = "qualified_name"


class NodeLabel(StrEnum):
    PROJECT = "Project"
    PACKAGE = "Package"
    FOLDER = "Folder"
    FILE = "File"
    MODULE = "Module"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"
    INTERFACE = "Interface"
    ENUM = "Enum"
    TYPE = "Type"
    UNION = "Union"
    MODULE_INTERFACE = "ModuleInterface"
    MODULE_IMPLEMENTATION = "ModuleImplementation"
    EXTERNAL_PACKAGE = "ExternalPackage"


class RelationshipType(StrEnum):
    CONTAINS_PACKAGE = "CONTAINS_PACKAGE"
    CONTAINS_FOLDER = "CONTAINS_FOLDER"
    CONTAINS_FILE = "CONTAINS_FILE"
    CONTAINS_MODULE = "CONTAINS_MODULE"
    DEFINES = "DEFINES"
    DEFINES_METHOD = "DEFINES_METHOD"
    IMPORTS = "IMPORTS"
    EXPORTS = "EXPORTS"
    EXPORTS_MODULE = "EXPORTS_MODULE"
    IMPLEMENTS_MODULE = "IMPLEMENTS_MODULE"
    INHERITS = "INHERITS"
    IMPLEMENTS = "IMPLEMENTS"
    OVERRIDES = "OVERRIDES"
    CALLS = "CALLS"
    DEPENDS_ON_EXTERNAL = "DEPENDS_ON_EXTERNAL"


class SupportedLanguage(StrEnum):
    PYTHON = "python"
    JS = "javascript"
    TS = "typescript"
    RUST = "rust"
    GO = "go"
    SCALA = "scala"
    JAVA = "java"
    C = "c"
    CPP = "cpp"
    CSHARP = "c-sharp"
    PHP = "php"
    LUA = "lua"


class LanguageStatus(StrEnum):
    FULL = "Fully Supported"
    DEV = "In Development"


class LanguageMetadata(NamedTuple):
    status: LanguageStatus
    additional_features: str
    display_name: str


LANGUAGE_METADATA: dict[SupportedLanguage, LanguageMetadata] = {
    SupportedLanguage.PYTHON: LanguageMetadata(
        LanguageStatus.FULL,
        "Type inference, decorators, nested functions",
        "Python",
    ),
    SupportedLanguage.JS: LanguageMetadata(
        LanguageStatus.FULL,
        "ES6 modules, CommonJS, prototype methods, object methods, arrow functions",
        "JavaScript",
    ),
    SupportedLanguage.TS: LanguageMetadata(
        LanguageStatus.FULL,
        "Interfaces, type aliases, enums, namespaces, ES6/CommonJS modules",
        "TypeScript",
    ),
    SupportedLanguage.CPP: LanguageMetadata(
        LanguageStatus.FULL,
        "Constructors, destructors, operator overloading, templates, lambdas, C++20 modules, namespaces",
        "C++",
    ),
    SupportedLanguage.LUA: LanguageMetadata(
        LanguageStatus.FULL,
        "Local/global functions, metatables, closures, coroutines",
        "Lua",
    ),
    SupportedLanguage.RUST: LanguageMetadata(
        LanguageStatus.FULL,
        "impl blocks, associated functions",
        "Rust",
    ),
    SupportedLanguage.JAVA: LanguageMetadata(
        LanguageStatus.FULL,
        "Generics, annotations, modern features (records/sealed classes), concurrency, reflection",
        "Java",
    ),
    SupportedLanguage.C: LanguageMetadata(
        LanguageStatus.FULL,
        "Functions, structs, unions, enums, function pointers",
        "C",
    ),
    SupportedLanguage.GO: LanguageMetadata(
        LanguageStatus.DEV,
        "Methods, type declarations",
        "Go",
    ),
    SupportedLanguage.SCALA: LanguageMetadata(
        LanguageStatus.DEV,
        "Case classes, objects",
        "Scala",
    ),
    SupportedLanguage.CSHARP: LanguageMetadata(
        LanguageStatus.DEV,
        "Classes, interfaces, generics (planned)",
        "C#",
    ),
    SupportedLanguage.PHP: LanguageMetadata(
        LanguageStatus.DEV,
        "Classes, functions, namespaces",
        "PHP",
    ),
}


# Node unique key mapping
_NODE_LABEL_UNIQUE_KEYS: dict[NodeLabel, UniqueKeyType] = {
    NodeLabel.PROJECT: UniqueKeyType.NAME,
    NodeLabel.PACKAGE: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.FOLDER: UniqueKeyType.PATH,
    NodeLabel.FILE: UniqueKeyType.PATH,
    NodeLabel.MODULE: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.CLASS: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.FUNCTION: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.METHOD: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.INTERFACE: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.ENUM: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.TYPE: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.UNION: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.MODULE_INTERFACE: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.MODULE_IMPLEMENTATION: UniqueKeyType.QUALIFIED_NAME,
    NodeLabel.EXTERNAL_PACKAGE: UniqueKeyType.NAME,
}

NODE_UNIQUE_CONSTRAINTS: dict[str, str] = {
    label.value: key.value for label, key in _NODE_LABEL_UNIQUE_KEYS.items()
}

# File extensions
EXT_PY = ".py"
EXT_JS = ".js"
EXT_JSX = ".jsx"
EXT_TS = ".ts"
EXT_TSX = ".tsx"
EXT_RS = ".rs"
EXT_GO = ".go"
EXT_SCALA = ".scala"
EXT_SC = ".sc"
EXT_JAVA = ".java"
EXT_C = ".c"
EXT_CPP = ".cpp"
EXT_H = ".h"
EXT_HPP = ".hpp"
EXT_CC = ".cc"
EXT_CXX = ".cxx"
EXT_HXX = ".hxx"
EXT_HH = ".hh"
EXT_IXX = ".ixx"
EXT_CPPM = ".cppm"
EXT_CCM = ".ccm"
EXT_CS = ".cs"
EXT_PHP = ".php"
EXT_LUA = ".lua"

# Extension tuples by language
PY_EXTENSIONS = (EXT_PY,)
JS_EXTENSIONS = (EXT_JS, EXT_JSX)
TS_EXTENSIONS = (EXT_TS, EXT_TSX)
RS_EXTENSIONS = (EXT_RS,)
GO_EXTENSIONS = (EXT_GO,)
SCALA_EXTENSIONS = (EXT_SCALA, EXT_SC)
JAVA_EXTENSIONS = (EXT_JAVA,)
C_EXTENSIONS = (EXT_C, EXT_H)
CPP_EXTENSIONS = (
    EXT_CPP, EXT_H, EXT_HPP, EXT_CC, EXT_CXX, EXT_HXX, EXT_HH, EXT_IXX, EXT_CPPM, EXT_CCM
)
CS_EXTENSIONS = (EXT_CS,)
PHP_EXTENSIONS = (EXT_PHP,)
LUA_EXTENSIONS = (EXT_LUA,)

# Package indicator files
PKG_INIT_PY = "__init__.py"
PKG_CARGO_TOML = "Cargo.toml"
PKG_CMAKE_LISTS = "CMakeLists.txt"
PKG_MAKEFILE = "Makefile"

# File names
INIT_PY = "__init__.py"
MOD_RS = "mod.rs"

# Encoding
ENCODING_UTF8 = "utf-8"

# Separators
SEPARATOR_DOT = "."
SEPARATOR_SLASH = "/"

# Path navigation
PATH_CURRENT_DIR = "."
PATH_PARENT_DIR = ".."
GLOB_ALL = "*"

# Trie internal keys
TRIE_TYPE_KEY = "__type__"
TRIE_QN_KEY = "__qn__"
TRIE_INTERNAL_PREFIX = "__"

# Property keys
KEY_NODES = "nodes"
KEY_RELATIONSHIPS = "relationships"
KEY_NODE_ID = "node_id"
KEY_LABELS = "labels"
KEY_PROPERTIES = "properties"
KEY_FROM_ID = "from_id"
KEY_TO_ID = "to_id"
KEY_TYPE = "type"
KEY_METADATA = "metadata"
KEY_TOTAL_NODES = "total_nodes"
KEY_TOTAL_RELATIONSHIPS = "total_relationships"
KEY_NODE_LABELS = "node_labels"
KEY_RELATIONSHIP_TYPES = "relationship_types"
KEY_EXPORTED_AT = "exported_at"
KEY_PARSER = "parser"
KEY_NAME = "name"
KEY_QUALIFIED_NAME = "qualified_name"
KEY_START_LINE = "start_line"
KEY_END_LINE = "end_line"
KEY_PATH = "path"
KEY_EXTENSION = "extension"
KEY_MODULE_TYPE = "module_type"
KEY_IMPLEMENTS_MODULE = "implements_module"
KEY_PROPS = "props"
KEY_CREATED = "created"
KEY_FROM_VAL = "from_val"
KEY_TO_VAL = "to_val"
KEY_VERSION_SPEC = "version_spec"
KEY_PREFIX = "prefix"
KEY_PROJECT_NAME = "project_name"
KEY_IS_EXTERNAL = "is_external"
KEY_PARAMETERS = "parameters"
KEY_DECORATORS = "decorators"
KEY_DOCSTRING = "docstring"
KEY_IS_EXPORTED = "is_exported"
KEY_RETURN_TYPE = "return_type"
KEY_SIGNATURE = "signature"
KEY_VISIBILITY = "visibility"
KEY_MEMBERS = "members"
KEY_KIND = "kind"

# Node type constants
NODE_PROJECT = NodeLabel.PROJECT
REL_TYPE_CALLS = "CALLS"

# Error substrings
ERR_SUBSTR_ALREADY_EXISTS = "already exists"
ERR_SUBSTR_CONSTRAINT = "constraint"

# Dependency files
DEPENDENCY_FILES = frozenset({
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "cargo.toml",
    "go.mod",
    "gemfile",
    "composer.json",
})
CSPROJ_SUFFIX = ".csproj"
EXCLUDED_DEPENDENCY_NAMES = frozenset({"python", "php"})

# Cypher queries
CYPHER_DEFAULT_LIMIT = 50

# Byte size constants
BYTES_PER_MB = 1024 * 1024

# Method signature formatting
EMPTY_PARENS = "()"
DOCSTRING_STRIP_CHARS = "'\" \\n"

# Inline module path prefix
INLINE_MODULE_PATH_PREFIX = "inline_module_"

# Index file names
INDEX_INIT = "__init__"
INDEX_INDEX = "index"
INDEX_MOD = "mod"

# AST field names for name extraction
NAME_FIELDS = ("identifier", "name", "id")

# Tree-sitter field name constants
FIELD_OBJECT = "object"
FIELD_PROPERTY = "property"
FIELD_NAME = "name"
FIELD_ALIAS = "alias"
FIELD_MODULE_NAME = "module_name"
FIELD_ARGUMENTS = "arguments"
FIELD_BODY = "body"
FIELD_CONSTRUCTOR = "constructor"
FIELD_DECLARATOR = "declarator"
FIELD_PARAMETERS = "parameters"
FIELD_TYPE = "type"
FIELD_VALUE = "value"
FIELD_LEFT = "left"
FIELD_RIGHT = "right"
FIELD_FIELD = "field"
FIELD_SUPERCLASS = "superclass"
FIELD_SUPERCLASSES = "superclasses"
FIELD_INTERFACES = "interfaces"

# Tree-sitter AST node type constants
FUNCTION_NODES_BASIC = ("function_declaration", "function_definition")
FUNCTION_NODES_LAMBDA = (
    "lambda_expression",
    "arrow_function",
    "anonymous_function",
    "closure_expression",
)
FUNCTION_NODES_METHOD = (
    "method_declaration",
    "constructor_declaration",
    "destructor_declaration",
)
FUNCTION_NODES_TEMPLATE = (
    "template_declaration",
    "function_signature_item",
    "function_signature",
)
FUNCTION_NODES_GENERATOR = ("generator_function_declaration", "function_expression")

CLASS_NODES_BASIC = ("class_declaration", "class_definition")
CLASS_NODES_STRUCT = ("struct_declaration", "struct_specifier", "struct_item")
CLASS_NODES_INTERFACE = ("interface_declaration", "trait_declaration", "trait_item")
CLASS_NODES_ENUM = ("enum_declaration", "enum_item", "enum_specifier")
CLASS_NODES_TYPE_ALIAS = ("type_alias_declaration", "type_item")
CLASS_NODES_UNION = ("union_specifier", "union_item")

CALL_NODES_BASIC = ("call_expression", "function_call")
CALL_NODES_METHOD = (
    "method_invocation",
    "member_call_expression",
    "field_expression",
)
CALL_NODES_OPERATOR = ("binary_expression", "unary_expression", "update_expression")
CALL_NODES_SPECIAL = ("new_expression", "delete_expression", "macro_invocation")

IMPORT_NODES_STANDARD = ("import_declaration", "import_statement")
IMPORT_NODES_FROM = ("import_from_statement",)
IMPORT_NODES_MODULE = ("lexical_declaration", "export_statement")
IMPORT_NODES_INCLUDE = ("preproc_include",)
IMPORT_NODES_USING = ("using_directive",)

# JS/TS specific node types
JS_TS_FUNCTION_NODES = (
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
)
JS_TS_CLASS_NODES = ("class_declaration", "class")
JS_TS_IMPORT_NODES = ("import_statement", "lexical_declaration", "export_statement")
JS_TS_LANGUAGES = frozenset({SupportedLanguage.JS, SupportedLanguage.TS})

# C++ import node types
CPP_IMPORT_NODES = ("preproc_include", "template_function", "declaration")

# Parser loader paths and args
GRAMMARS_DIR = "grammars"
TREE_SITTER_PREFIX = "tree-sitter-"
TREE_SITTER_MODULE_PREFIX = "tree_sitter_"
BINDINGS_DIR = "bindings"
SETUP_PY = "setup.py"
BUILD_EXT_CMD = "build_ext"
INPLACE_FLAG = "--inplace"
LANG_ATTR_PREFIX = "language_"
LANG_ATTR_TYPESCRIPT = "language_typescript"


class TreeSitterModule(StrEnum):
    PYTHON = "tree_sitter_python"
    JS = "tree_sitter_javascript"
    TS = "tree_sitter_typescript"
    RUST = "tree_sitter_rust"
    GO = "tree_sitter_go"
    SCALA = "tree_sitter_scala"
    JAVA = "tree_sitter_java"
    C = "tree_sitter_c"
    CPP = "tree_sitter_cpp"
    LUA = "tree_sitter_lua"


# Query dict keys
QUERY_FUNCTIONS = "functions"
QUERY_CLASSES = "classes"
QUERY_CALLS = "calls"
QUERY_IMPORTS = "imports"
QUERY_LOCALS = "locals"
QUERY_CONFIG = "config"
QUERY_LANGUAGE = "language"
QUERY_TYPEDEFS = "typedefs"
QUERY_MACROS = "macros"
QUERY_FUNC_PTR_ASSIGN = "func_ptr_assign"

# Query capture names
CAPTURE_FUNCTION = "function"
CAPTURE_CLASS = "class"
CAPTURE_CALL = "call"
CAPTURE_IMPORT = "import"
CAPTURE_IMPORT_FROM = "import_from"
CAPTURE_TYPEDEF = "typedef"
CAPTURE_MACRO = "macro"
CAPTURE_ASSIGN = "assign"
CAPTURE_LHS = "lhs"
CAPTURE_FIELD = "field"
CAPTURE_RHS = "rhs"

# Locals query patterns for JS/TS
JS_LOCALS_PATTERN = """
; Variable definitions
(variable_declarator name: (identifier) @local.definition)
(function_declaration name: (identifier) @local.definition)
(class_declaration name: (identifier) @local.definition)

; Variable references
(identifier) @local.reference
"""

TS_LOCALS_PATTERN = """
; Variable definitions (TypeScript has multiple declaration types)
(variable_declarator name: (identifier) @local.definition)
(lexical_declaration (variable_declarator name: (identifier) @local.definition))
(variable_declaration (variable_declarator name: (identifier) @local.definition))

; Function definitions
(function_declaration name: (identifier) @local.definition)

; Class definitions (uses type_identifier for class names)
(class_declaration name: (type_identifier) @local.definition)

; Variable references
(identifier) @local.reference
"""

# Ignore patterns
IGNORE_PATTERNS = frozenset({
    ".cache", ".claude", ".eclipse", ".eggs", ".env", ".git", ".gradle", ".hg",
    ".idea", ".maven", ".mypy_cache", ".nox", ".npm", ".nyc_output", ".pnpm-store",
    ".pytest_cache", ".qdrant_code_embeddings", ".ruff_cache", ".svn", ".tmp", ".tox",
    ".venv", ".vs", ".vscode", ".yarn", "__pycache__", "bin", "bower_components",
    "build", "coverage", "dist", "env", "htmlcov", "node_modules", "obj", "out",
    "Pods", "site-packages", "target", "temp", "tmp", "vendor", "venv",
})
IGNORE_SUFFIXES = frozenset({".tmp", "~", ".pyc", ".pyo", ".o", ".a", ".so", ".dll", ".class"})

# Binary extensions
BINARY_EXTENSIONS = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff", ".webp",
})

# Cypher response cleaning
CYPHER_PREFIX = "cypher"
CYPHER_SEMICOLON = ";"
CYPHER_BACKTICK = "`"
CYPHER_MATCH_KEYWORD = "MATCH"

# Method name constants
METHOD_FIND_WITH_PREFIX = "find_with_prefix"
METHOD_ITEMS = "items"

# JSON formatting
JSON_INDENT = 2

# Character constants
CHAR_HYPHEN = "-"
CHAR_UNDERSCORE = "_"
CHAR_SEMICOLON = ";"
CHAR_COMMA = ","
CHAR_COLON = ":"
CHAR_ANGLE_OPEN = "<"
CHAR_ANGLE_CLOSE = ">"
CHAR_PAREN_OPEN = "("
CHAR_PAREN_CLOSE = ")"
CHAR_SPACE = " "
SEPARATOR_COMMA_SPACE = ", "
PUNCTUATION_TYPES = (CHAR_PAREN_OPEN, CHAR_PAREN_CLOSE, CHAR_COMMA)

# Regex patterns
REGEX_METHOD_CHAIN_SUFFIX = r"\)\.[^)]*$"
REGEX_FINAL_METHOD_CAPTURE = r"\.([^.()]+)$"

# Default names
DEFAULT_NAME = "Unknown"
TEXT_UNKNOWN = "unknown"

# Language specs
SPEC_PY_FUNCTION_TYPES = (
    "function_definition",
    "lambda",
)
SPEC_PY_CLASS_TYPES = ("class_definition",)
SPEC_PY_MODULE_TYPES = ("module",)
SPEC_PY_CALL_TYPES = ("call",)
SPEC_PY_IMPORT_TYPES = ("import_statement",)
SPEC_PY_IMPORT_FROM_TYPES = ("import_from_statement",)
SPEC_PY_PACKAGE_INDICATORS = frozenset({PKG_INIT_PY})

SPEC_JS_MODULE_TYPES = ("program",)
SPEC_JS_CALL_TYPES = ("call_expression",)

SPEC_RS_FUNCTION_TYPES = (
    "function_item",
    "function_signature_item",
    "closure_expression",
)
SPEC_RS_CLASS_TYPES = (
    "struct_item",
    "enum_item",
    "union_item",
    "trait_item",
    "type_item",
    "impl_item",
)
SPEC_RS_MODULE_TYPES = ("mod_item", "source_file")
SPEC_RS_CALL_TYPES = ("call_expression", "macro_invocation")
SPEC_RS_IMPORT_TYPES = ("use_declaration",)
SPEC_RS_IMPORT_FROM_TYPES = ("use_declaration",)
SPEC_RS_PACKAGE_INDICATORS = frozenset({PKG_CARGO_TOML})

SPEC_GO_FUNCTION_TYPES = ("function_declaration", "method_declaration")
SPEC_GO_CLASS_TYPES = ("type_declaration",)
SPEC_GO_MODULE_TYPES = ("source_file",)
SPEC_GO_CALL_TYPES = ("call_expression",)
SPEC_GO_IMPORT_TYPES = ("import_declaration",)

SPEC_SCALA_FUNCTION_TYPES = (
    "function_definition",
    "macro_definition",
)
SPEC_SCALA_CLASS_TYPES = (
    "class_definition",
    "trait_definition",
    "object_definition",
    "enum_definition",
    "case_class_definition",
)
SPEC_SCALA_MODULE_TYPES = ("compilation_unit",)
SPEC_SCALA_CALL_TYPES = ("call_expression",)
SPEC_SCALA_IMPORT_TYPES = ("import_declaration",)

SPEC_JAVA_FUNCTION_TYPES = (
    "method_declaration",
    "constructor_declaration",
)
SPEC_JAVA_CLASS_TYPES = (
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "annotation_type_declaration",
    "record_declaration",
)
SPEC_JAVA_MODULE_TYPES = ("program",)
SPEC_JAVA_CALL_TYPES = ("method_invocation", "object_creation_expression")
SPEC_JAVA_IMPORT_TYPES = ("import_declaration",)

# C language specific
SPEC_C_FUNCTION_TYPES = (
    "function_definition",
    "declaration",
)
SPEC_C_CLASS_TYPES = (
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
)
SPEC_C_MODULE_TYPES = ("translation_unit",)
SPEC_C_CALL_TYPES = ("call_expression",)
SPEC_C_IMPORT_TYPES = ("preproc_include",)
SPEC_C_TYPEDEF_TYPES = ("type_definition",)
SPEC_C_MACRO_TYPES = ("preproc_def", "preproc_function_def")
SPEC_C_PACKAGE_INDICATORS = frozenset({PKG_MAKEFILE, "configure.ac", "configure.in"})

SPEC_CPP_FUNCTION_TYPES = (
    "function_definition",
    "template_declaration",
    "lambda_expression",
)
SPEC_CPP_CLASS_TYPES = (
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
)
SPEC_CPP_MODULE_TYPES = ("translation_unit",)
SPEC_CPP_CALL_TYPES = (
    "call_expression",
    "binary_expression",
    "unary_expression",
)
SPEC_CPP_IMPORT_TYPES = ("preproc_include",)
SPEC_CPP_PACKAGE_INDICATORS = frozenset({PKG_CMAKE_LISTS, PKG_MAKEFILE})

SPEC_CS_FUNCTION_TYPES = (
    "method_declaration",
    "constructor_declaration",
    "destructor_declaration",
)
SPEC_CS_CLASS_TYPES = (
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
)
SPEC_CS_MODULE_TYPES = ("compilation_unit",)
SPEC_CS_CALL_TYPES = ("invocation_expression", "object_creation_expression")

SPEC_PHP_FUNCTION_TYPES = ("function_definition", "method_declaration")
SPEC_PHP_CLASS_TYPES = ("class_declaration", "interface_declaration", "trait_declaration")
SPEC_PHP_MODULE_TYPES = ("program",)
SPEC_PHP_CALL_TYPES = ("function_call_expression", "member_call_expression")

SPEC_LUA_FUNCTION_TYPES = (
    "function_declaration",
    "local_function_declaration",
)
SPEC_LUA_CLASS_TYPES = ("table_constructor",)
SPEC_LUA_MODULE_TYPES = ("program",)
SPEC_LUA_CALL_TYPES = ("function_call",)
SPEC_LUA_IMPORT_TYPES = ("require_expression",)

# FQN scope types
FQN_PY_SCOPE_TYPES = ("class_definition", "function_definition")
FQN_PY_FUNCTION_TYPES = ("function_definition", "lambda")

FQN_JS_SCOPE_TYPES = ("class_declaration", "class", "function_declaration", "function_expression", "arrow_function", "method_definition")
FQN_JS_FUNCTION_TYPES = JS_TS_FUNCTION_NODES

FQN_TS_SCOPE_TYPES = FQN_JS_SCOPE_TYPES + ("interface_declaration", "enum_declaration", "type_alias_declaration")
FQN_TS_FUNCTION_TYPES = JS_TS_FUNCTION_NODES + ("function_signature",)

FQN_RS_SCOPE_TYPES = ("impl_item", "trait_item", "function_item", "mod_item")
FQN_RS_FUNCTION_TYPES = ("function_item", "function_signature_item", "closure_expression")

FQN_JAVA_SCOPE_TYPES = SPEC_JAVA_CLASS_TYPES
FQN_JAVA_FUNCTION_TYPES = SPEC_JAVA_FUNCTION_TYPES

FQN_CPP_SCOPE_TYPES = SPEC_CPP_CLASS_TYPES + ("namespace_definition",)
FQN_CPP_FUNCTION_TYPES = SPEC_CPP_FUNCTION_TYPES

FQN_LUA_SCOPE_TYPES = ("function_declaration", "local_function_declaration")
FQN_LUA_FUNCTION_TYPES = SPEC_LUA_FUNCTION_TYPES

FQN_GO_SCOPE_TYPES = ("type_declaration", "function_declaration")
FQN_GO_FUNCTION_TYPES = SPEC_GO_FUNCTION_TYPES

FQN_SCALA_SCOPE_TYPES = SPEC_SCALA_CLASS_TYPES
FQN_SCALA_FUNCTION_TYPES = SPEC_SCALA_FUNCTION_TYPES

FQN_CS_SCOPE_TYPES = SPEC_CS_CLASS_TYPES
FQN_CS_FUNCTION_TYPES = SPEC_CS_FUNCTION_TYPES

FQN_PHP_SCOPE_TYPES = SPEC_PHP_CLASS_TYPES + ("namespace_definition",)
FQN_PHP_FUNCTION_TYPES = SPEC_PHP_FUNCTION_TYPES

# Tree-sitter type constants
TS_TYPE_IDENTIFIER = "type_identifier"
TS_IDENTIFIER = "identifier"
TS_PY_EXPRESSION_STATEMENT = "expression_statement"
TS_PY_STRING = "string"
TS_CPP_FUNCTION_DEFINITION = "function_definition"
TS_CPP_FUNCTION_DECLARATOR = "function_declarator"

# JS name node types
JS_NAME_NODE_TYPES = JS_TS_FUNCTION_NODES + JS_TS_CLASS_NODES

# Rust type node types
RS_TYPE_NODE_TYPES = ("function_item", "struct_item", "enum_item", "trait_item", "impl_item", "type_item")
RS_IDENT_NODE_TYPES = ("mod_item",)

# C++ name node types
CPP_NAME_NODE_TYPES = SPEC_CPP_FUNCTION_TYPES + SPEC_CPP_CLASS_TYPES

# TS specific node types
TS_FUNCTION_SIGNATURE = "function_signature"
TS_ABSTRACT_CLASS_DECLARATION = "abstract_class_declaration"
TS_ENUM_DECLARATION = "enum_declaration"
TS_INTERFACE_DECLARATION = "interface_declaration"
TS_TYPE_ALIAS_DECLARATION = "type_alias_declaration"
TS_INTERNAL_MODULE = "internal_module"
