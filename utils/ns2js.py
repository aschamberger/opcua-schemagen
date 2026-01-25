from pathlib import Path
from typing import Any

from asyncua import ua
from asyncua.common.xmlparser import ExtObj, Field, NodeData
from rich import print

from utils.jsonschema import JSONSchemaBuilder
from utils.xmlparser import WrappedXMLParser

# FIXME proper source???
spec_desc = {
    "Machinery/Jobs": "OPC 40001-3: Machinery Job Mgmt",
}


class NodesetToJSONSchema:
    ua_base_model_URI: str = "http://opcfoundation.org/UA/"
    root_nodeset_ns_id: int = (
        1  # 0=OPC UA base nodeset, 1=main nodeset, n=other nodesets
    )
    base_types: list[str] = [
        "i=24",
        "i=58",
        "BaseDataType",
        "BaseObjectType",
    ]  # BaseDataType/BaseObjectType

    nodeset_uri_to_file: dict[str, Path] = {}
    nodeset_file_to_uri: dict[Path, str] = {}

    used_namespaces: dict[str, list[str]] = {}
    nodeset_namespaces: dict[str, list[tuple[str, ua.String, ua.DateTime]]] = {}
    aliases: dict[str, dict[str, str]] = {}
    nodes: dict[str, dict[str, NodeData]] = {}

    cloudevents_type_path: str
    cloudevents_dataschema_path: str
    schema: JSONSchemaBuilder

    def __init__(self, nodeset_path: Path, nodeset_file: Path, spec: str) -> None:
        self._nodeset_file_to_uri(nodeset_path)

        own_namespace = self.nodeset_file_to_uri[nodeset_file]
        self._import_nodeset(own_namespace)

        # FIXME handling versions???
        self.cloudevents_type_path = (
            f"com.github.aschamberger.ua.{spec.replace('/', '.')}"
        )
        self.cloudevents_dataschema_path = (
            f"https://aschamberger.github.com/schemas/UA/{spec}/"
        )

        model_any_of: list[str] = []
        self.schema = (
            JSONSchemaBuilder.start()
            .id(self.cloudevents_dataschema_path)
            .title(f"{spec_desc[spec]} for MQTT")
            .description(
                f"A JSON Schema to represent the {spec_desc[spec]} information model for a MQTT environment."
            )
            .definition("meta")
            .object()
            .prop("modelUri")
            .string()
            .default(self.nodeset_namespaces[own_namespace][0][0])
            .end()
            .prop("modelVersion")
            .string()
            .default(self.nodeset_namespaces[own_namespace][0][1])
            .end()
            .prop("modelDate")
            .format("date-time")
            .default(self.nodeset_namespaces[own_namespace][0][2].isoformat())
            .end()
            .prop("additionalAttributes")
            .array()
            .end()
            .end()
            .end()
        )
        model_any_of.append("meta")

        # manually add LocalizedText type, does not have definition in nodeset
        # maybe because it is a base type...
        self.schema = (
            self.schema.definition("LocalizedText")
            .object()
            .prop("Locale")
            .string()
            .end()
            .prop("Text")
            .string()
            .end()
            .end()
        )

        for ns in self.used_namespaces[own_namespace]:
            if ns != own_namespace and ns != self.ua_base_model_URI:
                for nodeid, node in self.nodes[ns].items():
                    if node.nodetype == "UADataType":
                        self._add_datatype(ns, node)

        # objects, methods, events
        for nodeid, node in self.nodes[own_namespace].items():
            match node.nodetype:
                case "UAObjectType":
                    # ignore "HasProperty" references as these only define constant properties of the object type
                    for object in WrappedXMLParser.get_node_children_by_ref_types(
                        node,
                        self.nodes[own_namespace],
                        reftypes=["HasComponent"],
                        nodetype="UAObject",
                    ):
                        datatype = WrappedXMLParser.get_node_type(object)
                        displayname = str(object.displayname)
                        if displayname not in model_any_of:
                            model_any_of.append(displayname)

                        # add objects to represent the variables
                        js_objecttype = self.schema.definition(displayname).object()
                        # resolve type hierarchy
                        js_objecttype = self._resolve_type_hierarchy_variables(
                            datatype, own_namespace, js_objecttype
                        )
                        # overwrite with own variables
                        variable_nodes = (
                            WrappedXMLParser.get_node_children_by_ref_types(
                                object,
                                self.nodes[own_namespace],
                                reftypes=["HasComponent", "HasProperty"],
                                nodetype="UAVariable",
                            )
                        )
                        js_objecttype = self._add_object_variables(
                            own_namespace, js_objecttype, variable_nodes
                        )
                        if object.desc:
                            js_objecttype = js_objecttype.description(object.desc)
                        # otherwise add description from parent object
                        elif not object.desc:
                            target_namespace_parent, node_id_parent = (
                                self._transform_node_id(
                                    WrappedXMLParser.get_node_type(object),
                                    own_namespace,
                                )
                            )
                            parent_object = self.nodes[target_namespace_parent].get(
                                node_id_parent
                            )
                            if parent_object and parent_object.desc:
                                js_objecttype = js_objecttype.description(
                                    parent_object.desc
                                )
                        self.schema = (
                            js_objecttype.set(
                                "x-cloudevent-type",
                                f"{self.cloudevents_type_path}.{displayname}",
                            )
                            .set(
                                "x-cloudevent-dataschema",
                                f"{self.cloudevents_dataschema_path}{displayname}/",
                            )
                            .set("x-opc-ua-type", "DataSet")
                            .end()
                        )

                        # add methods
                        js_methodtype = self._resolve_type_hierarchy_methods(
                            datatype, own_namespace, self.schema, model_any_of
                        )
                        method_nodes = WrappedXMLParser.get_node_children_by_ref_types(
                            object,
                            self.nodes[own_namespace],
                            reftypes=["HasComponent", "HasProperty"],
                            nodetype="UAMethod",
                        )
                        self.schema = self._add_methods(
                            own_namespace, js_methodtype, method_nodes, model_any_of
                        )

                        # add events
                        js_eventtype = self._resolve_type_hierarchy_events(
                            datatype, own_namespace, self.schema, model_any_of
                        )
                        event_nodes = WrappedXMLParser.get_node_children_by_ref_types(
                            object,
                            self.nodes[own_namespace],
                            reftypes=["GeneratesEvent"],
                            nodetype="UAObjectType",
                        )
                        self.schema = self._add_events(
                            own_namespace, js_eventtype, event_nodes, model_any_of
                        )

                case "UADataType":
                    self._add_datatype(own_namespace, node)

                case "UAObject":
                    # ignore the main object and only "instantate" the object types
                    pass

        if model_any_of:
            js_model = self.schema.any_of()  # type: ignore
            for ref in model_any_of:
                js_model = js_model.add().ref(ref).end()
            self.schema = js_model.end()

    def save_schema(self, path: Path):
        self.schema.save(path, indent=2)

    def _nodeset_file_to_uri(self, nodeset_path: Path) -> None:
        # Find the nodeset file corresponding to the model URI
        for nodeset in nodeset_path.glob("**/*ode*et2.xml"):
            try:
                temp_parser = WrappedXMLParser()
                temp_parser.parse_sync(nodeset)
                namespaces = temp_parser.get_nodeset_namespaces()
                for namespace in namespaces:
                    self.nodeset_uri_to_file[namespace[0]] = nodeset
                    self.nodeset_file_to_uri[nodeset] = namespace[0]
            except Exception as e:
                print(f"[red]Error parsing file {nodeset}: {e}[/red]")

    def _import_nodeset(self, uri: str) -> None:
        nodeset_file = self.nodeset_uri_to_file.get(uri)
        if not nodeset_file:
            print(f"[red]Nodeset file for URI {uri} not found![/red]")
            return
        parser = WrappedXMLParser()
        parser.parse_sync(nodeset_file)
        nodeset_namespaces = parser.get_nodeset_namespaces()
        namespace = nodeset_namespaces[0][0]

        self.used_namespaces[namespace] = parser.get_used_namespaces()
        self.nodeset_namespaces[namespace] = nodeset_namespaces
        self.aliases[namespace] = parser.get_aliases()
        self.nodes[namespace] = parser.get_node_data_dict()

        required_models = WrappedXMLParser.list_required_models(nodeset_file)
        for model in required_models:
            self._import_nodeset(model["ModelUri"])

    def _ua_basictype_to_schema_basictype(self, type: str) -> str:
        # defined from https://reference.opcfoundation.org/Core/Part5/v105/docs/12
        match type:
            # 12.2.2 Boolean
            case "i=1":
                return "boolean"
            # 12.2.3 ByteString
            case (
                "i=15" | "i=16307" | "i=30" | "i=2000" | "i=2001" | "i=2002" | "i=2003"
            ):
                return "string"
            # 12.2.4 DateTime
            case "i=13" | "i=294":
                return "date-time"
            # 12.2.5 Enumeration
            case "i=29":  # only base here, subtypes have definitions
                return "enum"
            # 12.2.6 Guid
            case "i=14":
                return "uuid"
            # 12.2.7 LocalizedText
            # added manually to "$defs" as it has no definition in nodeset
            # 12.2.8 NodeId
            case "i=17":
                return "string"
            # 12.2.9 Number
            case (
                "i=2"
                | "i=3"
                | "i=4"
                | "i=5"
                | "i=6"
                | "i=7"
                | "i=8"
                | "i=9"
                | "i=27"
                | "i=28"
                | "i=95"
            ):
                return "integer"
            case "i=10" | "i=11" | "i=50" | "i=290":
                return "number"
            # 12.2.10 QualifiedName
            case "i=20":
                return "string"
            # 12.2.11 String
            case (
                "i=12"
                | "i=295"
                | "i=12877"
                | "i=12878"
                | "i=12879"
                | "i=12880"
                | "i=12881"
                | "i=12882"
            ):
                return "string"
            case _:
                return "object"

    def _transform_node_id(self, nodeid: str, own_namespace: str) -> tuple[str, str]:
        # transform nodeid to use the correct namespace index
        parts = nodeid.split(";")
        if len(parts) != 2:
            return self.ua_base_model_URI, nodeid
        ns_part = parts[0]
        id_part = parts[1]
        ns_index_source = int(ns_part[3:])
        target_namespace = self.used_namespaces[own_namespace][
            ns_index_source - 1
        ]  # array is 0-based, ns=0 is implicit
        ns_index_target = (
            self.used_namespaces[target_namespace].index(target_namespace) + 1
        )
        return target_namespace, f"ns={ns_index_target};{id_part}"

    def _resolve_type_hierarchy_variables(
        self, datatype: str, own_namespace: str, js_objecttype: JSONSchemaBuilder
    ) -> JSONSchemaBuilder:
        target_namespace, node_id_target = self._transform_node_id(
            datatype, own_namespace
        )
        object = self.nodes[target_namespace].get(node_id_target)
        if object:
            datatype2 = WrappedXMLParser.get_node_type(object)
            if datatype2 not in self.base_types:  # BaseDataType/BaseObjectType
                js_objecttype = self._resolve_type_hierarchy_variables(
                    datatype2, target_namespace, js_objecttype
                )

            child_nodes = WrappedXMLParser.get_node_children_by_ref_types(
                object,
                self.nodes[target_namespace],
                reftypes=["HasComponent", "HasProperty"],
                nodetype="UAVariable",
            )
            js_objecttype = self._add_object_variables(
                target_namespace, js_objecttype, child_nodes
            )
        return js_objecttype

    def _add_object_variables(
        self,
        own_namespace: str,
        js_objecttype: JSONSchemaBuilder,
        child_nodes: list[NodeData],
    ) -> JSONSchemaBuilder:
        for child_node in child_nodes:
            if child_node.nodetype == "UAVariable":
                js_objecttype = js_objecttype.prop(str(child_node.displayname))
                datatype = self.aliases[own_namespace].get(
                    str(child_node.datatype), str(child_node.datatype)
                )
                variable_type = self._ua_basictype_to_schema_basictype(datatype)
                if child_node.rank >= 1:
                    js_objecttype = js_objecttype.array()
                match variable_type:
                    case "object" | "enum":
                        object_name = str(child_node.datatype)
                        if object_name not in self.base_types:
                            js_objecttype = js_objecttype.ref(object_name)
                    case "date-time" | "uuid":
                        js_objecttype = js_objecttype.string().format(variable_type)
                    case _:
                        js_objecttype = getattr(js_objecttype, variable_type)()
                if child_node.rank >= 1:
                    js_objecttype = js_objecttype.end()
                if child_node.desc:
                    js_objecttype = js_objecttype.description(child_node.desc)
                js_objecttype = js_objecttype.end()
        return js_objecttype

    def _resolve_type_hierarchy_methods(
        self,
        datatype: str,
        own_namespace: str,
        js_methodtype: JSONSchemaBuilder,
        model_any_of: list[str],
    ) -> JSONSchemaBuilder:
        target_namespace, node_id_target = self._transform_node_id(
            datatype, own_namespace
        )
        object = self.nodes[target_namespace].get(node_id_target)
        if object:
            datatype2 = WrappedXMLParser.get_node_type(object)
            if datatype2 != "i=24":  # BaseDataType
                js_methodtype = self._resolve_type_hierarchy_methods(
                    datatype2, target_namespace, js_methodtype, model_any_of
                )

            child_nodes = WrappedXMLParser.get_node_children_by_ref_types(
                object,
                self.nodes[target_namespace],
                reftypes=["HasComponent", "HasProperty"],
                nodetype="UAMethod",
            )
            js_methodtype = self._add_methods(
                target_namespace, js_methodtype, child_nodes, model_any_of
            )
        return js_methodtype

    def _add_methods(
        self,
        own_namespace: str,
        js_methodtype: JSONSchemaBuilder,
        child_nodes: list[NodeData],
        model_any_of: list[str],
    ) -> JSONSchemaBuilder:
        for child_node in child_nodes:
            if child_node.nodetype == "UAMethod":
                child_nodes2 = WrappedXMLParser.get_node_children_by_ref_types(
                    child_node,
                    self.nodes[own_namespace],
                    reftypes=["HasProperty"],
                    nodetype="UAVariable",
                )
                for arg_node in child_nodes2:
                    if arg_node.displayname == "InputArguments":
                        method = f"{str(child_node.displayname)}Call"
                    else:
                        method = f"{str(child_node.displayname)}Response"
                    if method not in model_any_of:
                        model_any_of.append(method)
                    js_methodtype = self.schema.definition(method).object()

                    if arg_node.value is not None:
                        value: list[ExtObj] = arg_node.value
                        if isinstance(value, list):
                            for ext_obj in value:
                                ext_obj_data: dict[str, Any] = dict(ext_obj.body[0][1])
                                js_methodtype = js_methodtype.prop(
                                    str(ext_obj_data["Name"])
                                )
                                datatype_dict = dict(ext_obj_data["DataType"])
                                datatype = self.aliases[own_namespace].get(
                                    str(datatype_dict["Identifier"]),
                                    str(datatype_dict["Identifier"]),
                                )
                                variable_type = self._ua_basictype_to_schema_basictype(
                                    datatype
                                )
                                if int(ext_obj_data["ValueRank"]) >= 1:
                                    js_methodtype = js_methodtype.array()
                                match variable_type:
                                    case "object" | "enum":
                                        object_name_list = [
                                            key
                                            for key, value in self.aliases[
                                                own_namespace
                                            ].items()
                                            if value == str(datatype_dict["Identifier"])
                                        ]
                                        if len(object_name_list) == 1:
                                            object_name = object_name_list[0]
                                        else:
                                            ns, node_id = self._transform_node_id(
                                                str(datatype_dict["Identifier"]),
                                                own_namespace,
                                            )
                                            object_node = self.nodes[ns].get(node_id)
                                            object_name = (
                                                str(object_node.displayname)
                                                if object_node
                                                else str(datatype_dict["Identifier"])
                                            )
                                        if object_name not in self.base_types:
                                            js_methodtype = js_methodtype.ref(
                                                object_name
                                            )
                                    case "date-time" | "uuid":
                                        js_methodtype = js_methodtype.string().format(
                                            variable_type
                                        )
                                    case _:
                                        js_methodtype = getattr(
                                            js_methodtype, variable_type
                                        )()
                                if int(ext_obj_data["ValueRank"]) >= 1:
                                    js_methodtype = js_methodtype.end()
                                if ext_obj_data["Description"]:
                                    description_dict = dict(ext_obj_data["Description"])
                                    js_methodtype = js_methodtype.description(
                                        description_dict["Text"]
                                    )
                                js_methodtype = js_methodtype.end()

                    if child_node.desc:
                        js_methodtype = js_methodtype.description(child_node.desc)
                    if arg_node.displayname == "InputArguments":
                        response = f"{str(child_node.displayname)}Response"
                        js_methodtype = js_methodtype.set(
                            "x-response-type",
                            f"{self.cloudevents_type_path}.{response}",
                        )
                    js_methodtype = (
                        js_methodtype.set(
                            "x-cloudevent-type",
                            f"{self.cloudevents_type_path}.{method}",
                        )
                        .set(
                            "x-cloudevent-dataschema",
                            f"{self.cloudevents_dataschema_path}{method}/",
                        )
                        .set("x-opc-ua-type", "Method")
                        .end()
                        .end()
                    )
        return js_methodtype

    def _resolve_type_hierarchy_events(
        self,
        datatype: str,
        own_namespace: str,
        js_methodtype: JSONSchemaBuilder,
        model_any_of: list[str],
    ) -> JSONSchemaBuilder:
        target_namespace, node_id_target = self._transform_node_id(
            datatype, own_namespace
        )
        object = self.nodes[target_namespace].get(node_id_target)
        if object:
            datatype2 = WrappedXMLParser.get_node_type(object)
            if datatype2 not in self.base_types:  # BaseDataType/BaseObjectType
                js_methodtype = self._resolve_type_hierarchy_events(
                    datatype2, target_namespace, js_methodtype, model_any_of
                )

            child_nodes = WrappedXMLParser.get_node_children_by_ref_types(
                object,
                self.nodes[target_namespace],
                reftypes=["GeneratesEvent"],
                nodetype="UAObjectType",
            )
            js_methodtype = self._add_events(
                target_namespace, js_methodtype, child_nodes, model_any_of
            )
        return js_methodtype

    def _add_events(
        self,
        own_namespace: str,
        js_eventtype: JSONSchemaBuilder,
        child_nodes,
        model_any_of: list[str],
    ) -> JSONSchemaBuilder:
        for child_node in child_nodes:
            if child_node.nodetype == "UAObjectType":
                displayname = str(child_node.displayname)
                if displayname not in model_any_of:
                    model_any_of.append(displayname)
                js_eventtype = self.schema.definition(displayname).object()
                child_nodes2 = WrappedXMLParser.get_node_children_by_ref_types(
                    child_node,
                    self.nodes[own_namespace],
                    reftypes=["HasProperty"],
                    nodetype="UAVariable",
                )
                self._add_object_variables(own_namespace, js_eventtype, child_nodes2)
                if child_node.desc:
                    js_eventtype = js_eventtype.description(child_node.desc)
                js_eventtype = (
                    js_eventtype.set(
                        "x-cloudevent-type",
                        f"{self.cloudevents_type_path}.{displayname}",
                    )
                    .set(
                        "x-cloudevent-dataschema",
                        f"{self.cloudevents_dataschema_path}{displayname}/",
                    )
                    .set("x-opc-ua-type", "Event")
                    .end()
                    .end()
                )
        return js_eventtype

    def _resolve_and_add_datatype(self, own_namespace: str, object_name: str) -> None:
        object_node_id = self.aliases[own_namespace][object_name]
        target_namespace, node_id_target = self._transform_node_id(
            object_node_id, own_namespace
        )
        object_node = self.nodes[target_namespace].get(node_id_target)
        if object_node:
            self._add_datatype(target_namespace, object_node)

    def _add_datatype(self, own_namespace: str, node: NodeData) -> None:
        if node.struct_type == "IsOptionSet":
            # define mask type
            js_masks = self.schema.definition(f"{str(node.displayname)}Masks").any_of()
            for field in node.definitions:
                js_masks = (
                    js_masks.add()
                    .const(2**field.value)  # bit mask value
                    .title(field.name)
                    .description(field.desc)
                    .end()
                )
            self.schema = js_masks.end().end()

        js_datatype = self.schema.definition(str(node.displayname))
        datatype = WrappedXMLParser.get_node_type(node)
        schema_type = self._ua_basictype_to_schema_basictype(datatype)
        match schema_type:
            case "date-time" | "uuid":
                js_datatype = js_datatype.string().format(schema_type)
            case "enum":
                js_enum = (
                    js_datatype.any_of()
                )  # don't use the basic enum type as we have name+value
                field: Field
                for field in node.definitions:
                    js_enum = (
                        js_enum.add()
                        .const(field.value)
                        .title(field.name)
                        .description(field.desc)
                        .end()
                    )
                js_datatype = js_enum.end()
            case _:
                js_datatype = getattr(js_datatype, schema_type)()
                if node.struct_type != "IsOptionSet":
                    field: Field
                    for field in node.definitions:
                        js_datatype = js_datatype.prop(field.name)
                        datatype = self.aliases[own_namespace].get(
                            field.datatype, field.datatype
                        )
                        field_type = self._ua_basictype_to_schema_basictype(
                            str(datatype)
                        )
                        if field.valuerank >= 1:
                            js_datatype = js_datatype.array()
                        match field_type:
                            case "object" | "enum":
                                field_object_name = str(field.datatype)
                                field_node_id = self.aliases[own_namespace].get(
                                    field_object_name, field_object_name
                                )
                                if (
                                    field_node_id not in self.base_types
                                    and field_node_id.startswith("i=")
                                ):
                                    target_namespace, node_id_target = (
                                        self._transform_node_id(
                                            field_node_id, own_namespace
                                        )
                                    )
                                    object_node = self.nodes[target_namespace].get(
                                        node_id_target
                                    )
                                    if object_node:
                                        self._add_datatype(
                                            target_namespace, object_node
                                        )

                                # look up object name from aliases
                                if field_object_name.startswith(
                                    "ns="
                                ) or field_object_name.startswith("i="):
                                    aliasname_list = [
                                        key
                                        for key, value in self.aliases[
                                            own_namespace
                                        ].items()
                                        if value == field_object_name
                                    ]
                                    if len(aliasname_list) == 1:
                                        field_object_name = aliasname_list[0]
                                    else:
                                        ns, node_id = self._transform_node_id(
                                            field_object_name, own_namespace
                                        )
                                        object_node = self.nodes[ns].get(node_id)
                                        field_object_name = (
                                            str(object_node.displayname)
                                            if object_node
                                            else field_object_name
                                        )

                                if field_object_name not in self.base_types:
                                    js_datatype = js_datatype.ref(field_object_name)
                            case "date-time" | "uuid":
                                js_datatype = js_datatype.string().format(field_type)
                            case _:
                                js_datatype = getattr(js_datatype, field_type)()
                        if field.optional:
                            js_datatype = js_datatype.nullable()
                        if field.valuerank >= 1:
                            js_datatype = js_datatype.end()
                        if field.desc:
                            js_datatype = js_datatype.description(field.desc)
                        js_datatype = js_datatype.end()
        if node.desc:
            js_datatype = js_datatype.description(node.desc)
        self.schema = js_datatype.end()
