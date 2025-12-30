import pathlib
import sys

import typer

# Add parent directory to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import nodeset
import schema

app = typer.Typer()
app.add_typer(
    schema.app, name="schema", help="Generate python dataclasses from JSON Schema."
)
app.add_typer(
    nodeset.app,
    name="nodeset",
    help="Process OPC UA Nodeset XML files (analyze, JSON Schema generation, ...).",
)

if __name__ == "__main__":
    app()
