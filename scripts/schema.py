import pathlib
import sys
from typing import Annotated
import typer
from rich import print
from rich.table import Table

# Add parent directory to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

app = typer.Typer()

schemas_path = pathlib.Path(__file__).parent.parent / "schemas"

@app.command()
def index():
    print(f"[bold purple]Searching schema files ...[/bold purple]")
    parent_path_len = len(str(schemas_path)) + 1

    table = Table()
    table.add_column("File", style="cyan", no_wrap=True)

    for schema_file in sorted(schemas_path.glob(f"**/*.json")):
        file_size = schema_file.stat().st_size
        table.add_row(str(schema_file)[parent_path_len:])
    print(table)

@app.command()
def dataclass(schema_file: Annotated[pathlib.Path, typer.Argument()]):
    print(f"[bold purple]Using schema file: {schema_file}[/bold purple]")
    print()
    print(f"[bold red]Not yet implemented[/bold red]\n")


if __name__ == "__main__":
    app()