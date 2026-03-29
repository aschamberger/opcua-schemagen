import html as html_module
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from asyncua import ua
from asyncua.common.xmlparser import ExtObj, Field, NodeData
from rich import print

from opcua_schemagen.jsonschema import JSONSchemaBuilder
from opcua_schemagen.xmlparser import WrappedXMLParser


class _HeadlineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_p_h3 = False
        self._texts: list[str] = []
        self._h1s: list[str] = []

    def handle_starttag(self, tag: str, attrs):  # type: ignore[override]
        tag_lower = tag.lower()
        if tag_lower == "p":
            for name, value in attrs:
                if name.lower() == "class" and value and "h3" in value.split():
                    self._in_p_h3 = True
                    self._texts = []
                    return

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "p" and self._in_p_h3:
            text = html_module.unescape(" ".join(self._texts)).strip()
            if text:
                self._h1s.append(text)
            self._texts = []
            self._in_p_h3 = False

    def handle_data(self, data: str) -> None:
        if self._in_p_h3:
            self._texts.append(data)

    def first_h1_containing(self, needle: str) -> str | None:
        for text in self._h1s:
            if needle in text:
                return text
        return None


def _extract_docs_base_url(nodeset_file: Path) -> str | None:
    try:
        content = nodeset_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    for match in re.finditer(
        r"<Documentation>(.*?)</Documentation>", content, re.DOTALL
    ):
        url = match.group(1).strip()
        if "docs/" in url:
            base = url.split("docs/")[0] + "docs/"
            return base
    return None


def get_spec_title(nodeset_file: Path) -> str | None:
    docs_url = _extract_docs_base_url(nodeset_file)
    if not docs_url:
        return None

    try:
        with urllib.request.urlopen(docs_url, timeout=10) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            html_text = response.read().decode(charset, errors="ignore")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None

    parser = _HeadlineParser()
    parser.feed(html_text)
    return parser.first_h1_containing("OPC")


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

    cloudevents_type_path: tuple[str, str]
    cloudevents_dataschema_path: str
    schema: JSONSchemaBuilder

    def __init__(
        self,
        nodeset_path: Path,
        nodeset_file: Path,
        spec: str,
        nodeid_replacements: list[tuple[str, str]] | None = None,
        include_object_namespaces: list[str] | None = None,
    ) -> None:
        self._nodeid_replacements = nodeid_replacements or []
        self._nodeset_file_to_uri(nodeset_path)

        own_namespace = self.nodeset_file_to_uri[nodeset_file]
        self._import_nodeset(own_namespace)

        self.cloudevents_type_path = self._namespace_to_cloudevents_type_path(
            own_namespace
        )
        self.cloudevents_dataschema_path = (
            self._namespace_to_cloudevents_dataschema_path(own_namespace)
        )

        spec_title = get_spec_title(nodeset_file)
        model_any_of: list[str] = []
        self.schema = (
            JSONSchemaBuilder.start()
            .id(self.cloudevents_dataschema_path)
            .title(f"{spec_title} for MQTT")
            .description(
                f"A JSON Schema to represent the {spec_title} information model for a MQTT environment."
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

        # own datatypes first so they take precedence over parent definitions
        for nodeid, node in self.nodes[own_namespace].items():
            if node.nodetype == "UADataType":
                self._add_datatype(own_namespace, node)

        # parent/dependency datatypes (skipped if already defined by own namespace)
        for ns in self.used_namespaces[own_namespace]:
            if ns != own_namespace and ns != self.ua_base_model_URI:
                for nodeid, node in self.nodes[ns].items():
                    if node.nodetype == "UADataType":
                        self._add_datatype(ns, node)

        # objects, methods, events from included parent namespaces (before own, so own can override)
        for included_ns in include_object_namespaces or []:
            self._process_object_types(included_ns, model_any_of)

        # objects, methods, events from own namespace (last wins for overrides)
        self._process_object_types(own_namespace, model_any_of)

        if model_any_of:
            js_model = self.schema.any_of()  # type: ignore
            for ref in model_any_of:
                js_model = js_model.add().ref(ref).end()
            self.schema = js_model.end()

    def _process_object_types(self, namespace: str, model_any_of: list[str]) -> None:
        for nodeid, node in self.nodes[namespace].items():
            match node.nodetype:
                case "UAObjectType":
                    # ignore "HasProperty" references as these only define constant properties of the object type
                    for object in WrappedXMLParser.get_node_children_by_ref_types(
                        node,
                        self.nodes[namespace],
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
                            datatype, namespace, js_objecttype
                        )
                        # overwrite with own variables
                        variable_nodes = (
                            WrappedXMLParser.get_node_children_by_ref_types(
                                object,
                                self.nodes[namespace],
                                reftypes=["HasComponent", "HasProperty"],
                                nodetype="UAVariable",
                            )
                        )
                        js_objecttype = self._add_object_variables(
                            namespace, js_objecttype, variable_nodes
                        )
                        if object.desc:
                            js_objecttype = js_objecttype.description(object.desc)
                        # otherwise add description from parent object
                        elif not object.desc:
                            target_namespace_parent, node_id_parent = (
                                self._transform_node_id(
                                    WrappedXMLParser.get_node_type(object),
                                    namespace,
                                )
                            )
                            parent_object = self.nodes[target_namespace_parent].get(
                                node_id_parent
                            )
                            if parent_object and parent_object.desc:
                                js_objecttype = js_objecttype.description(
                                    parent_object.desc
                                )
                        ce_base, ce_ver = self._namespace_to_cloudevents_type_path(
                            namespace
                        )
                        ce_ds = self._namespace_to_cloudevents_dataschema_path(
                            namespace
                        )
                        js_objecttype = (
                            js_objecttype.set(
                                "x-cloudevent-type",
                                f"{ce_base}.{displayname}.{ce_ver}",
                            )
                            .set(
                                "x-cloudevent-dataschema",
                                f"{ce_ds}{displayname}/",
                            )
                            .set("x-opc-ua-type", "DataSet")
                        )
                        state_machine = self._resolve_type_hierarchy_states(
                            datatype, namespace
                        )
                        if state_machine["states"] or state_machine["transitions"]:
                            js_objecttype = js_objecttype.set(
                                "x-opc-ua-state-machine", state_machine
                            )
                        self.schema = js_objecttype.end()

                        # add methods
                        js_methodtype = self._resolve_type_hierarchy_methods(
                            datatype, namespace, self.schema, model_any_of
                        )
                        method_nodes = WrappedXMLParser.get_node_children_by_ref_types(
                            object,
                            self.nodes[namespace],
                            reftypes=["HasComponent", "HasProperty"],
                            nodetype="UAMethod",
                        )
                        self.schema = self._add_methods(
                            namespace, js_methodtype, method_nodes, model_any_of
                        )

                        # add events
                        js_eventtype = self._resolve_type_hierarchy_events(
                            datatype, namespace, self.schema, model_any_of
                        )
                        event_nodes = WrappedXMLParser.get_node_children_by_ref_types(
                            object,
                            self.nodes[namespace],
                            reftypes=["GeneratesEvent"],
                            nodetype="UAObjectType",
                        )
                        self.schema = self._add_events(
                            namespace, js_eventtype, event_nodes, model_any_of
                        )

                case "UADataType":
                    self._add_datatype(namespace, node)

                case "UAObject":
                    # ignore the main object and only "instantate" the object types
                    pass

    def save_schema(self, path: Path):
        self.schema.save(path, indent=2)

    def _namespace_to_cloudevents_type_path(
        self, namespace_uri: str
    ) -> tuple[str, str]:
        """Convert a namespace URI to a CloudEvent type base path and version suffix."""
        path = namespace_uri.replace(self.ua_base_model_URI, "").strip("/")
        if not path:
            path = "BaseModel"
        model_version = str(self.nodeset_namespaces[namespace_uri][0][1])
        major_version = model_version.split(".")[0]
        return f"org.opcfoundation.{path.replace('/', '.')}", f"v{major_version}"

    def _namespace_to_cloudevents_dataschema_path(self, namespace_uri: str) -> str:
        """Convert a namespace URI to a CloudEvent dataschema base URL including version."""
        path = namespace_uri.replace(self.ua_base_model_URI, "").strip("/")
        if not path:
            path = "BaseModel"
        model_version = str(self.nodeset_namespaces[namespace_uri][0][1])
        return f"https://aschamberger.github.com/schemas/UA/{path}/v{model_version}/"

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
        if self._nodeid_replacements:
            xml_content = nodeset_file.read_bytes()
            for find_text, replace_text in self._nodeid_replacements:
                xml_content = xml_content.replace(
                    find_text.encode("utf-8"), replace_text.encode("utf-8")
                )
            parser.parse_sync(xmlstring=xml_content)
        else:
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
            self._check_required_model(model)

    @staticmethod
    def _parse_version(version: str) -> tuple[int, ...]:
        """Parse a version string like '1.04.11' into a tuple of ints for comparison."""
        try:
            return tuple(int(part) for part in version.split("."))
        except (ValueError, AttributeError):
            return (0,)

    @staticmethod
    def _parse_publication_date(date_str: str) -> datetime:
        """Parse a publication date string into a timezone-aware datetime."""
        if date_str.endswith("Z"):
            return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S%z")

    def _check_required_model(self, model: dict[str, str]) -> None:
        """Check that the loaded model satisfies the minimum version and publication date."""
        required_uri = model["ModelUri"]
        if required_uri not in self.nodeset_namespaces:
            return

        loaded = self.nodeset_namespaces[required_uri][0]
        loaded_version = str(loaded[1])
        loaded_date: datetime = loaded[2]  # type: ignore[assignment]

        required_version = model.get("Version", "")
        required_date_str = model.get("PublicationDate", "")

        if required_version and loaded_version:
            if self._parse_version(loaded_version) < self._parse_version(
                required_version
            ):
                print(
                    f"[yellow]Warning: {required_uri} requires minimum version "
                    f"{required_version}, but loaded version is {loaded_version}[/yellow]"
                )

        if required_date_str:
            required_date = self._parse_publication_date(required_date_str)
            # normalize loaded_date to timezone-aware for comparison
            comparable_date = (
                loaded_date.replace(tzinfo=timezone.utc)
                if loaded_date.tzinfo is None
                else loaded_date
            )
            if comparable_date < required_date:
                print(
                    f"[yellow]Warning: {required_uri} requires minimum publication date "
                    f"{required_date_str}, but loaded date is {loaded_date.isoformat()}[/yellow]"
                )

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

    def _resolve_type_hierarchy_states(
        self, datatype: str, own_namespace: str
    ) -> dict[str, dict[str, dict[str, Any]]]:
        target_namespace, node_id_target = self._transform_node_id(
            datatype, own_namespace
        )
        state_machine: dict[str, dict[str, dict[str, Any]]] = {
            "states": {},
            "transitions": {},
        }
        object = self.nodes[target_namespace].get(node_id_target)
        if object:
            datatype2 = WrappedXMLParser.get_node_type(object)
            if datatype2 not in self.base_types:  # BaseDataType/BaseObjectType
                state_machine = self._resolve_type_hierarchy_states(
                    datatype2, target_namespace
                )

            self.populate_state_machine(target_namespace, state_machine, object)
        return state_machine

    def populate_state_machine(self, target_namespace, state_machine, object):
        child_nodes = WrappedXMLParser.get_node_children_by_ref_types(
            object,
            self.nodes[target_namespace],
            reftypes=["HasComponent"],
            nodetype="UAObject",
        )

        for child_node in child_nodes:
            name = str(child_node.displayname)
            item: dict[str, Any] = {
                "description": child_node.desc,
            }

            for ref in child_node.refs:
                if ref.reftype == "HasProperty":
                    property_node = self.nodes[target_namespace].get(str(ref.target))
                    if property_node and property_node.value is not None:
                        item["number"] = property_node.value
                elif child_node.typedef == "i=2307":  # StateType
                    match ref.reftype:
                        case "HasSubStateMachine":
                            property_node = self.nodes[target_namespace].get(
                                str(ref.target)
                            )
                            if property_node:
                                item["subStateMachine"] = {
                                    "description": str(property_node.desc),
                                    "states": {},
                                    "transitions": {},
                                }
                                submachine = (
                                    WrappedXMLParser.get_node_children_by_ref_types(
                                        property_node,
                                        self.nodes[target_namespace],
                                        reftypes=["HasTypeDefinition"],
                                        nodetype="UAObjectType",
                                    )[0]
                                )
                                self.populate_state_machine(
                                    target_namespace,
                                    item["subStateMachine"],
                                    submachine,
                                )
                elif child_node.typedef == "i=2310":  # TransitionType
                    match ref.reftype:
                        case "FromState":
                            node = self.nodes[target_namespace].get(str(ref.target))
                            if node:
                                item["fromState"] = str(node.displayname)
                        case "ToState":
                            node = self.nodes[target_namespace].get(str(ref.target))
                            if node:
                                item["toState"] = str(node.displayname)
                        case "HasEffect":
                            property_node = self.nodes[target_namespace].get(
                                str(ref.target)
                            )
                            if property_node:
                                item["effect"] = str(property_node.displayname)
                        case "HasCause":
                            property_node = self.nodes[target_namespace].get(
                                str(ref.target)
                            )
                            if property_node:
                                item["cause"] = str(property_node.displayname)
            match child_node.typedef:
                case "i=2307":  # StateType
                    state_machine["states"][name] = item
                case "i=2310":  # TransitionType
                    state_machine["transitions"][name] = item

    def _add_object_variables(
        self,
        own_namespace: str,
        js_objecttype: JSONSchemaBuilder,
        child_nodes: list[NodeData],
    ) -> JSONSchemaBuilder:
        for child_node in child_nodes:
            if child_node.nodetype == "UAVariable":
                # skip placeholder variables (OptionalPlaceholder=i=11508, MandatoryPlaceholder=i=11510)
                if any(
                    ref.reftype == "HasModellingRule"
                    and ref.target in ("i=11508", "i=11510")
                    for ref in child_node.refs
                ):
                    continue
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
                for ref in child_node.refs:
                    if (
                        ref.reftype == "HasModellingRule" and ref.target == "i=80"
                    ):  # Optional
                        js_objecttype = js_objecttype.nullable()
                        break
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
                        js_methodtype = js_methodtype.set(
                            "x-response-type",
                            {"$ref": f"#/$defs/{str(child_node.displayname)}Response"},
                        )
                    else:
                        js_methodtype = js_methodtype.set(
                            "x-request-type",
                            {"$ref": f"#/$defs/{str(child_node.displayname)}Call"},
                        )
                    ce_base, ce_ver = self._namespace_to_cloudevents_type_path(
                        own_namespace
                    )
                    ce_ds = self._namespace_to_cloudevents_dataschema_path(
                        own_namespace
                    )
                    js_methodtype = (
                        js_methodtype.set(
                            "x-cloudevent-type",
                            f"{ce_base}.{method}.{ce_ver}",
                        )
                        .set(
                            "x-cloudevent-dataschema",
                            f"{ce_ds}{method}/",
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
                ce_base, ce_ver = self._namespace_to_cloudevents_type_path(
                    own_namespace
                )
                ce_ds = self._namespace_to_cloudevents_dataschema_path(own_namespace)
                js_eventtype = (
                    js_eventtype.set(
                        "x-cloudevent-type",
                        f"{ce_base}.{displayname}.{ce_ver}",
                    )
                    .set(
                        "x-cloudevent-dataschema",
                        f"{ce_ds}{displayname}/",
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
        name = str(node.displayname)
        root_defs = self.schema._root_ref._schema.get("$defs", {})
        if name in root_defs and root_defs[name]:
            return  # already defined, skip to avoid duplicates

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
                                if field_node_id not in self.base_types and (
                                    field_node_id.startswith("i=")
                                    or field_node_id.startswith("ns=")
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
        ce_base, ce_ver = self._namespace_to_cloudevents_type_path(own_namespace)
        ce_ds = self._namespace_to_cloudevents_dataschema_path(own_namespace)
        js_datatype = js_datatype.set(
            "x-cloudevent-type",
            f"{ce_base}.{node.displayname}.{ce_ver}",
        ).set(
            "x-cloudevent-dataschema",
            f"{ce_ds}{node.displayname}/",
        )
        self.schema = js_datatype.end()
