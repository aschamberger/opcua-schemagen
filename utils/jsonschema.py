from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Self, Union


# --- Exceptions ---
class FluentSchemaError(Exception):
    """Base exception for the JSONSchemaBuilder."""
    pass

class SchemaValidationError(FluentSchemaError):
    """Raised when an invalid constraint is applied to a type."""
    pass

class JSONSchemaBuilder:
    def __init__(
        self,
        schema: Dict[str, Any],
        parent: Optional[Union[JSONSchemaBuilder, LogicalContext]] = None,
        root: Optional[JSONSchemaBuilder] = None
    ) -> None:
        self._schema = schema
        self._parent = parent
        self._root_ref = root if root is not None else self

    @classmethod
    def start(cls) -> Self:
        """EntryPoint: Initializes a fresh root schema."""
        return cls(schema={"$schema": "https://json-schema.org/draft/2020-12/schema"})

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Self:
        """Factory: Build on top of an existing dictionary."""
        return cls(schema=deepcopy(data))

    def _ensure_type(self, *allowed_types: str, method_name: str) -> None:
        """Internal guard using 3.14 structural pattern matching."""
        if "type" not in self._schema and "object" in allowed_types:
            self.object()
        match self._schema.get("type"):
            case list() as types:
                if not any(t in types for t in allowed_types):
                    raise SchemaValidationError(f"'{method_name}' requires {allowed_types}, found {types}.")
            case str() as t if t not in allowed_types:
                raise SchemaValidationError(f"'{method_name}' requires {allowed_types}, found '{t}'.")

    # --- 1. The Build Function with Topological Sort ---
    def _extract_refs(self, schema: Any, refs: set[str]) -> None:
        """Helper to find all local $defs references in a schema node."""
        if isinstance(schema, dict):
            if "$ref" in schema:
                ref = schema["$ref"]
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    refs.add(ref.split("/")[-1])
            for val in schema.values():
                self._extract_refs(val, refs)
        elif isinstance(schema, list):
            for item in schema:
                self._extract_refs(item, refs)

    def build(self) -> Dict[str, Any]:
        """Builds the schema and topologically sorts $defs based on dependencies."""
        full_schema = deepcopy(self._root_ref._schema)
        defs = full_schema.get("$defs", {})
        if not defs: return full_schema

        graph: Dict[str, set[str]] = {name: set() for name in defs}
        for name, subschema in defs.items():
            self._extract_refs(subschema, graph[name])

        sorted_names, visited, temp_stack = [], set(), set()

        def visit(node: str):
            if node in temp_stack: return # Handle/allow recursion in Draft 2020-12
            if node not in visited:
                temp_stack.add(node)
                if node in graph:
                    for neighbor in graph[node]:
                        visit(neighbor)
                temp_stack.remove(node)
                visited.add(node)
                sorted_names.append(node)

        for name in defs:
            if name not in visited: visit(name)

        full_schema["$defs"] = {name: defs[name] for name in sorted_names}
        return full_schema

    # --- 2. Structural & Root ---
    def id(self, schema_id: str) -> Self: self._schema["$id"] = schema_id; return self
    def object(self) -> Self: self._schema["type"] = "object"; self._schema.setdefault("properties", {}); return self
    def array(self) -> JSONSchemaBuilder:
        self._schema["type"] = "array"
        self._schema["items"] = {}
        return JSONSchemaBuilder(self._schema["items"], parent=self, root=self._root_ref)

    def prop(self, name: str, required: bool = False) -> JSONSchemaBuilder:
        if "type" not in self._schema: self.object()
        props = self._schema.setdefault("properties", {})
        props.setdefault(name, {})
        if required: self.required_props([name])
        return JSONSchemaBuilder(props[name], parent=self, root=self._root_ref)

    def required_props(self, names: List[str]) -> Self:
        if self._schema.get("type") != "object": self.object()
        reqs = self._schema.setdefault("required", [])
        for name in names:
            if name not in reqs: reqs.append(name)
        return self

    # --- 3. Logic & Reusability ---
    def definition(self, name: str) -> JSONSchemaBuilder:
        root_s = self._root_ref._schema
        if "$defs" not in root_s: root_s["$defs"] = {}
        root_s["$defs"].setdefault(name, {})
        return JSONSchemaBuilder(root_s["$defs"][name], parent=self, root=self._root_ref)

    def ref(self, reference: str) -> Self:
        if any(reference.startswith(p) for p in ("http://", "https://", "#", "urn:")):
            self._schema["$ref"] = reference
        else:
            self._schema["$ref"] = f"#/$defs/{reference}"
        return self

    def _logic(self, key: str) -> LogicalContext:
        logic_list = self._schema.setdefault(key, [])
        return LogicalContext(logic_list, parent=self, root=self._root_ref)

    def any_of(self) -> LogicalContext: return self._logic("anyOf")
    def one_of(self) -> LogicalContext: return self._logic("oneOf")
    def all_of(self) -> LogicalContext: return self._logic("allOf")

    # --- 4. Constraints Suite ---
    def string(self) -> Self: self._schema["type"] = "string"; return self
    def integer(self) -> Self: self._schema["type"] = "integer"; return self
    def number(self) -> Self: self._schema["type"] = "number"; return self
    def boolean(self) -> Self: self._schema["type"] = "boolean"; return self
    def const(self, value: Any) -> Self: self._schema["const"] = value; return self
    def nullable(self) -> Self:
        match self._schema.get("type"):
            case list() as types:
                if "null" not in types: types.append("null")
            case str() as t if t != "null":
                self._schema["type"] = [t, "null"]
        return self

    # String Specifics
    def min_length(self, n: int) -> Self: self._ensure_type("string", method_name="min_length"); self._schema["minLength"] = n; return self
    def max_length(self, n: int) -> Self: self._ensure_type("string", method_name="max_length"); self._schema["maxLength"] = n; return self
    def pattern(self, r: str) -> Self: self._ensure_type("string", method_name="pattern"); self._schema["pattern"] = r; return self
    def format(self, f: str) -> Self: self._ensure_type("string", method_name="format"); self._schema["format"] = f; return self

    # Number Specifics
    def minimum(self, n: float) -> Self: self._ensure_type("number", "integer", method_name="minimum"); self._schema["minimum"] = n; return self
    def maximum(self, n: float) -> Self: self._ensure_type("number", "integer", method_name="maximum"); self._schema["maximum"] = n; return self
    def multiple_of(self, n: float) -> Self: self._ensure_type("number", "integer", method_name="multiple_of"); self._schema["multipleOf"] = n; return self

    # Array Specifics
    def min_items(self, n: int) -> Self: self._ensure_type("array", method_name="min_items"); self._schema["minItems"] = n; return self
    def max_items(self, n: int) -> Self: self._ensure_type("array", method_name="max_items"); self._schema["maxItems"] = n; return self
    def unique_items(self, b: bool = True) -> Self: self._ensure_type("array", method_name="unique_items"); self._schema["uniqueItems"] = b; return self

    # --- 5. Annotations & Vendor ---
    def title(self, t: str) -> Self: self._schema["title"] = t; return self
    def description(self, d: str) -> Self: self._schema["description"] = d; return self
    def default(self, v: Any) -> Self: self._schema["default"] = v; return self
    def enum(self, v: List[Any]) -> Self: self._schema["enum"] = list(v); return self
    def deprecated(self, b: bool = True) -> Self: self._schema["deprecated"] = b; return self

    def set(self, key: str, value: Any) -> Self: self._schema[key] = value; return self

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"): raise AttributeError(f"No attribute {name}")
        if name.startswith("x_"):
            def wrapper(value: Any) -> Self: return self.set(name.replace("_", "-"), value)
            return wrapper
        raise AttributeError(f"No attribute {name}")

    # --- 6. Execution ---
    def end(self) -> Any: return self._parent if self._parent else self
    def to_json(self, indent: int = 2) -> str: return json.dumps(self.build(), indent=indent)
    def save(self, path: Path, indent: int = 2) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(indent=indent), encoding="utf-8")

class LogicalContext:
    def __init__(self, list_ref: List[Dict[str, Any]], parent: JSONSchemaBuilder, root: JSONSchemaBuilder) -> None:
        self._list, self._parent, self._root = list_ref, parent, root
    def add(self) -> JSONSchemaBuilder:
        branch_schema: Dict[str, Any] = {}
        self._list.append(branch_schema)
        return JSONSchemaBuilder(schema=branch_schema, parent=self, root=self._root)
    def end(self) -> JSONSchemaBuilder: return self._parent