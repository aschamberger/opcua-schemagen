import pathlib
import typing
from typing import Any

from asyncua.common.xmlparser import NodeData, RefStruct, XMLParser


class WrappedXMLParser(XMLParser):
    # new: add a static method to get node type
    @staticmethod
    def get_node_type(node: NodeData) -> str:
        reference: RefStruct
        for reference in node.refs:
            if reference.reftype == "HasSubtype" and not reference.forward:
                return str(reference.target)
            elif reference.reftype == "HasTypeDefinition" and reference.forward:
                return str(reference.target)
        return "i=24"  # BaseDataType

    # new: add a static method to get child nodes by ref types
    @staticmethod
    def get_node_children_by_ref_types(
        node: NodeData,
        nodes: dict[str, NodeData],
        reftypes: list[str] = ["HasComponent", "HasProperty"],
        is_forward: bool = True,
        nodetype: str = "UAObject",
    ) -> list[NodeData]:
        children: list[NodeData] = []
        reference: RefStruct
        for reference in node.refs:
            if reference.reftype in reftypes and reference.forward == is_forward:
                child_node = nodes.get(str(reference.target))
                if child_node and child_node.nodetype == nodetype:
                    children.append(child_node)
        return children

    # decorate with type hints
    @staticmethod
    def list_required_models(
        xmlpath: pathlib.Path, xmlstring: pathlib.Path | None = None
    ) -> list[dict[str, Any]]:
        return XMLParser.list_required_models(xmlpath, xmlstring)

    def __init__(self):
        super().__init__()

    # decorate with type hints and ensure non-None return values
    def get_used_namespaces(self) -> list[str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        result: list[str] = []
        for ns in super().get_used_namespaces():
            if ns is not None:
                result.append(ns)
        return result

    # decorate with type hints
    def get_aliases(self) -> dict[str, str]:
        return super().get_aliases()

    # decorate with type hints and ensure non-None return values
    def get_node_datas(self) -> list[NodeData]:
        result = super().get_node_datas()
        return typing.cast(list[NodeData], result) if result is not None else []

    # override to parse additional attributes
    def _parse_attr(self, el, obj):
        super()._parse_attr(el, obj)

        tag = self._retag.match(el.tag).groups()[1]  # pyright: ignore[reportOptionalMemberAccess]
        if tag == "Definition":
            if el.attrib.get("IsOptionSet", False):
                obj.struct_type = "IsOptionSet"
        elif tag == "Documentation":
            if obj.desc:
                obj.desc = f"{obj.desc}\n{el.text}"
            else:
                obj.desc = el.text

    # new: add a helper method to get node data as a dict
    def get_node_data_dict(self) -> dict[str, NodeData]:
        result: dict[str, NodeData] = {}
        for node in self.get_node_datas():
            if node.nodeid is not None:
                result[node.nodeid] = node
        return result
