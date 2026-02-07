import pathlib
import re
import sys
from typing import Annotated

import typer
from asyncua.common.xmlparser import Field, RefStruct
from rich import print
from rich.table import Table

# Add parent directory to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from utils.ns2js import NodesetToJSONSchema
from utils.xmlparser import WrappedXMLParser

app = typer.Typer()

main_path = pathlib.Path(__file__).parent.parent / "UA-Nodeset"
schemas_path = pathlib.Path(__file__).parent.parent / "schemas"


def get_nodeset_file_from_spec_path(spec: str) -> pathlib.Path:
    nodeset_path = main_path / spec
    nodeset_file = next(nodeset_path.glob("*ode*et2.xml"))
    return nodeset_file


@app.command()
def index():
    print("[bold purple]Searching companion specs (nodeset xml) ...[/bold purple]")
    parent_path_len = len(str(main_path)) + 1

    table = Table(title="Nodesets:", show_lines=True)
    table.add_column("Folder", style="cyan", no_wrap=True)
    table.add_column("Namespaces", style="magenta")

    for nodeset_file in sorted(
        main_path.glob("**/*ode*et2.xml")
    ):  # filename can be both lowercase or uppercase 'N/S'
        ns_table = Table()
        try:
            parser = WrappedXMLParser()
            parser.parse_sync(nodeset_file)

            ns_table = Table(show_header=False)
            ns_table.add_column("Namespace", style="cyan", no_wrap=True)
            ns_table.add_column("Version", style="magenta")
            ns_table.add_column("PublicationDate", style="green", no_wrap=True)
            nodeset_namespaces = parser.get_nodeset_namespaces()
            for namespace in nodeset_namespaces:
                ns_table.add_row(namespace[0], namespace[1], str(namespace[2]))
        except Exception as e:
            ns_table.add_row(f"[red]Error parsing file: {e}[/red]", "", "")
        table.add_row(str(nodeset_file.parent)[parent_path_len:], ns_table)
    print(table)


@app.command()
def info(spec: Annotated[str, typer.Argument()] = "ISA95-JOBCONTROL"):
    print(f"[bold purple]Searching nodeset2.xml file for: {spec}[/bold purple]")
    nodeset_file = get_nodeset_file_from_spec_path(spec)
    print(f"Using file: {nodeset_file}")
    print()

    parser = WrappedXMLParser()
    parser.parse_sync(nodeset_file)

    table = Table(title="Used Namespaces:")
    table.add_column("Namespace", style="cyan", no_wrap=True)
    used_namespaces = parser.get_used_namespaces()
    for namespace in used_namespaces:
        table.add_row(namespace)
    print(table)

    table = Table(title="Nodeset Namespaces:")
    table.add_column("Namespace", style="cyan", no_wrap=True)
    table.add_column("Version", style="magenta")
    table.add_column("PublicationDate", style="green")
    nodeset_namespaces = parser.get_nodeset_namespaces()
    for namespace in nodeset_namespaces:
        table.add_row(namespace[0], namespace[1], str(namespace[2]))
    print(table)

    table = Table(title="Required Models:")
    table.add_column("ModelUri", style="cyan", no_wrap=True)
    table.add_column("Version", style="magenta")
    table.add_column("PublicationDate", style="green")
    required_models = WrappedXMLParser.list_required_models(nodeset_file)
    for model in required_models:
        table.add_row(model["ModelUri"], model["Version"], model["PublicationDate"])
    print(table)

    table = Table(title="Aliases:")
    table.add_column("Alias", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta")
    aliases: dict[str, str] = parser.get_aliases()
    for key, value in aliases.items():
        table.add_row(key, value)
    print(table)


def format_references(refs: list[RefStruct], show: bool = False) -> Table | str:
    if not show:
        return "[white]<hidden>[/white]"

    table = Table()
    table.add_column("RefType", style="cyan", no_wrap=True)
    table.add_column("IsForward", style="magenta", no_wrap=True)
    table.add_column("Target", style="green", no_wrap=True)
    for ref in refs:
        table.add_row(ref.reftype, str(ref.forward), ref.target)
    return table


def format_definitions(definitions: list[Field], show: bool = False) -> Table | str:
    if not show:
        return "[white]<hidden>[/white]"

    table = Table()
    table.add_column("Type", style="green", no_wrap=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    field: Field
    for field in definitions:
        table.add_row(field.datatype, field.name)
    return table


@app.command()
def objects(
    spec: Annotated[str, typer.Argument()] = "ISA95-JOBCONTROL",
    children: Annotated[bool, typer.Option(help="Show child elements.")] = True,
    references: Annotated[bool, typer.Option(help="Show references tables.")] = True,
):
    print(f"[bold purple]Searching nodeset2.xml file for: {spec}[/bold purple]")
    nodeset_file = get_nodeset_file_from_spec_path(spec)
    print(f"Using file: {nodeset_file}")
    print()

    parser = WrappedXMLParser()
    parser.parse_sync(nodeset_file)

    table = Table(title="Object Types:", show_lines=True)
    table.add_column("NodeType", style="cyan", no_wrap=True)
    table.add_column("NodeId", style="cyan", no_wrap=True)
    table.add_column("BrowseName", style="magenta")
    table.add_column("DisplayName", style="magenta")
    table.add_column("References", style="green", no_wrap=True)
    nodes = parser.get_node_data_dict()
    for nodeid, node in nodes.items():
        if node.nodetype == "UAObjectType":
            table.add_row(
                node.nodetype,
                node.nodeid,
                node.browsename,
                node.displayname,
                format_references(node.refs, references),
            )
            if children:
                for child_node in WrappedXMLParser.get_node_children_by_ref_types(
                    node, nodes
                ):
                    table.add_row(
                        f"  {child_node.nodetype}",
                        child_node.nodeid,
                        child_node.browsename,
                        child_node.displayname,
                        format_references(child_node.refs, references)
                        if references
                        else None,
                    )
    print(table)


@app.command()
def types(
    spec: Annotated[str, typer.Argument()] = "ISA95-JOBCONTROL",
    references: Annotated[bool, typer.Option(help="Show references tables.")] = True,
    definitions: Annotated[bool, typer.Option(help="Show definitions tables.")] = True,
):
    print(f"[bold purple]Searching nodeset2.xml file for: {spec}[/bold purple]")
    nodeset_file = get_nodeset_file_from_spec_path(spec)
    print(f"Using file: {nodeset_file}")
    print()

    parser = WrappedXMLParser()
    parser.parse_sync(nodeset_file)

    table = Table(title="Data Types:", show_lines=True)
    table.add_column("NodeType", style="cyan", no_wrap=True)
    table.add_column("NodeId", style="cyan", no_wrap=True)
    table.add_column("BrowseName", style="magenta")
    table.add_column("DisplayName", style="magenta")
    table.add_column("Definitions", style="blue")
    table.add_column("References", style="green")
    nodes = parser.get_node_data_dict()
    for nodeid, node in nodes.items():
        if node.nodetype == "UADataType":
            table.add_row(
                node.nodetype,
                node.nodeid,
                node.browsename,
                node.displayname,
                format_definitions(node.definitions, definitions),
                format_references(node.refs, references),
            )
    print(table)


@app.command()
def appschema(
    spec: Annotated[str, typer.Argument()] = "ISA95-JOBCONTROL",
    filename: Annotated[
        str, typer.Argument(help="Output filename for JSON Schema.")
    ] = "",
    nodeid_replace: Annotated[
        list[str],
        typer.Option(
            "--nodeid-replace",
            help="Replace NodeId values in Nodeset XML (format: FROM->TO). Can be used multiple times.",
        ),
    ] = [],
):
    print(f"[bold purple]Searching nodeset2.xml file for: {spec}[/bold purple]")
    nodeset_file = get_nodeset_file_from_spec_path(spec)
    print(f"Using file: {nodeset_file}")
    print()

    replacements: list[tuple[str, str]] = []
    for item in nodeid_replace:
        if "->" in item:
            find_text, replace_text = item.split("->", 1)
            replacements.append((find_text, replace_text))
    ns2js = NodesetToJSONSchema(
        main_path, nodeset_file, spec, nodeid_replacements=replacements
    )
    if filename == "":
        filename = f"{spec.lower().replace('/', '_')}.jsonschema.json"
    outfile = schemas_path / filename
    ns2js.save_schema(outfile)
    print(f"[bold green]JSON Schema saved to {outfile}[/bold green]\n")


if __name__ == "__main__":
    app()
