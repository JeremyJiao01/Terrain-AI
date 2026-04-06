/**
 * CodeGraphWiki Demo Data
 * Pre-loaded data for the interactive product website.
 * No API calls required. Now reflecting the actual code_graph_builder repo!
 */

window.demoData = {
  // 4.3 Pipeline Animation Steps
  pipeline: [
    { step: "graph-build", text: "Parsed 124 functions, 48 call relationships in code_graph_builder", progress: 100 },
    { step: "api-doc-gen", text: "Generated API docs for 124 functions", progress: 100 },
    { step: "embed-gen", text: "Embeddings via Qwen/BGE ready, semantic search enabled", progress: 100 }
  ],

  // 4.4 Trace Call Investigation Scenario
  investigation: {
    startLog: "ERROR in GraphUpdater.run() at line 89",
    callChain: [
      { func: "main", file: "entrypoints/cli/commands_cli.py", line: 1157 },
      { func: "cmd_graph_build", file: "entrypoints/cli/commands_cli.py", line: 272 },
      { func: "CodeGraphBuilder.build_graph", file: "domains/core/graph/builder.py", line: 213 },
      { func: "GraphUpdater.run", file: "domains/core/graph/graph_updater.py", line: 89 }
    ],
    worksheet: {
      title: "GraphUpdater.run — Investigation Worksheet",
      sections: [
        {
          id: "call-chain",
          title: "Call Chain",
          content: "main (commands_cli.py:1157) → cmd_graph_build (commands_cli.py:272) → CodeGraphBuilder.build_graph (builder.py:213) → GraphUpdater.run (graph_updater.py:89)",
          isOpen: true
        },
        {
          id: "trigger-conditions",
          title: "Trigger Conditions",
          content: "Triggered when the user runs the `code-graph-builder graph-build` CLI command. Initiates the parsing and indexing of the target repository into KuzuDB.",
          isOpen: false
        },
        {
          id: "possible-causes",
          title: "Possible Causes",
          content: "1. Kuzu database lock file leftover from a previous crashed run\n2. Tree-sitter parser failed to initialize due to missing language binding\n3. Embedding API token exhausted or network timeout",
          isOpen: false
        },
        {
          id: "related-functions",
          title: "Related Functions",
          content: "CodeGraphBuilder._get_ingestor(), CodeGraphBuilder._load_parsers(). Click these to navigate the call tree.",
          isOpen: false,
          links: ["CodeGraphBuilder._get_ingestor", "CodeGraphBuilder._load_parsers"]
        }
      ]
    }
  },

  // 4.5 Call Chain Browser Data (Actual Python Project: code_graph_builder)
  callTree: {
    name: "main",
    signature: "def main() -> None:",
    file: "entrypoints/cli/commands_cli.py",
    line: 1157,
    children: [
      {
        name: "cmd_init",
        signature: "def cmd_init(args: argparse.Namespace, ws: Workspace) -> None:",
        file: "entrypoints/cli/commands_cli.py",
        line: 151,
        children: [
          { name: "Workspace.set_active", signature: "def set_active(self, artifact_dir: Path) -> None:", file: "entrypoints/cli/commands_cli.py", line: 78 }
        ]
      },
      {
        name: "cmd_graph_build",
        signature: "def cmd_graph_build(args: argparse.Namespace, ws: Workspace) -> None:",
        file: "entrypoints/cli/commands_cli.py",
        line: 272,
        children: [
          {
            name: "CodeGraphBuilder.build_graph",
            signature: "def build_graph(self, clean: bool = False) -> BuildResult:",
            file: "domains/core/graph/builder.py",
            line: 213,
            children: [
              { name: "CodeGraphBuilder._load_parsers", signature: "def _load_parsers(self) -> None:", file: "domains/core/graph/builder.py", line: 158 },
              { name: "CodeGraphBuilder._get_ingestor", signature: "def _get_ingestor(self) -> MemgraphIngestor | KuzuIngestor | Any:", file: "domains/core/graph/builder.py", line: 164 },
              { 
                name: "GraphUpdater.run", 
                signature: "def run(self) -> None:", 
                file: "domains/core/graph/graph_updater.py", 
                line: 89,
                children: [
                    { name: "GraphUpdater._process_file", signature: "def _process_file(self, file_path: Path) -> None:", file: "domains/core/graph/graph_updater.py", line: 120 }
                ]
              }
            ]
          }
        ]
      },
      {
        name: "cmd_api_doc_gen",
        signature: "def cmd_api_doc_gen(args: argparse.Namespace, ws: Workspace) -> None:",
        file: "entrypoints/cli/commands_cli.py",
        line: 322,
        children: [
          { name: "ApiDocGenerator.generate", signature: "def generate(self, filter_paths: list[str] = None) -> None:", file: "domains/upper/apidoc/api_doc_generator.py", line: 45 }
        ]
      },
      {
        name: "cmd_embed_gen",
        signature: "def cmd_embed_gen(args: argparse.Namespace, ws: Workspace) -> None:",
        file: "entrypoints/cli/commands_cli.py",
        line: 1099,
        children: [
          { name: "_load_vector_store", signature: "def _load_vector_store(vectors_path: Path):", file: "entrypoints/cli/commands_cli.py", line: 122 }
        ]
      }
    ]
  }
};
