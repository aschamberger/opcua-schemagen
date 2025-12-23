from __future__ import annotations
import json
from pathlib import Path
from copy import deepcopy
from typing import Any, Dict, List, Optional, Union, Self

# --- Exceptions ---
class FluentSchemaError(Exception):
    """Base exception for the Fluent JSON Schema builder."""
    pass

class SchemaValidationError(FluentSchemaError):
    """Raised when an invalid constraint is applied to a type."""
    pass

class JSONSchemaBuilder:
    def __init__(
        self,
        schema: Optional[Dict[str, Any]] = None,
        parent: Optional[Union[JSONSchemaBuilder, LogicalContext]] = None,
        root: Optional[JSONSchemaBuilder] = None
    ) -> None:
        self._root_ref: JSONSchemaBuilder = root if root is not None else self
        self._schema: Dict[str, Any] = schema if schema is not None else {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {}
        }
        self._parent = parent

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Self:
        return cls(schema=deepcopy(data))

    def _ensure_type(self, *allowed_types: str, method_name: str) -> None:
        if "type" not in self._schema and "object" in allowed_types:
            self.object()
        match self._schema.get("type"):
            case list() as types:
                if not any(t in types for t in allowed_types):
                    raise SchemaValidationError(f"'{method_name}' requires {allowed_types}, found {types}.")
            case str() as t if t not in allowed_types:
                raise SchemaValidationError(f"'{method_name}' requires {allowed_types}, found '{t}'.")

    # --- 1. Structural & Root Helpers ---
    def id(self, schema_id: str) -> Self:
        self._schema["$id"] = schema_id
        return self

    def object(self) -> Self:
        self._schema["type"] = "object"
        self._schema.setdefault("properties", {})
        return self

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

    # --- 2. Logic & Reusability (Updated Ref) ---
    def definition(self, name: str) -> JSONSchemaBuilder:
        root_defs = self._root_ref._schema.setdefault("$defs", {})
        root_defs.setdefault(name, {})
        return JSONSchemaBuilder(root_defs[name], parent=self, root=self._root_ref)

    def ref(self, reference: str) -> Self:
        """
        Supports internal references (definitions) and external URIs.
        - 'myType' -> '#/$defs/myType'
        - 'https://example.com/other.json' -> 'https://example.com/other.json'
        - '#/relative/path' -> '#/relative/path'
        """
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

    # --- 3. Types & Constraints ---
    def string(self) -> Self: self._schema["type"] = "string"; return self
    def integer(self) -> Self: self._schema["type"] = "integer"; return self
    def number(self) -> Self: self._schema["type"] = "number"; return self
    def boolean(self) -> Self: self._schema["type"] = "boolean"; return self

    def nullable(self) -> Self:
        match self._schema.get("type"):
            case list() as types:
                if "null" not in types: types.append("null")
            case str() as t if t != "null":
                self._schema["type"] = [t, "null"]
        return self

    def min_length(self, n: int) -> Self:
        self._ensure_type("string", method_name="min_length")
        self._schema["minLength"] = n; return self
    def max_length(self, n: int) -> Self:
        self._ensure_type("string", method_name="max_length")
        self._schema["maxLength"] = n; return self
    def pattern(self, r: str) -> Self:
        self._ensure_type("string", method_name="pattern")
        self._schema["pattern"] = r; return self
    def format(self, f: str) -> Self:
        self._ensure_type("string", method_name="format")
        self._schema["format"] = f; return self

    def minimum(self, n: float) -> Self:
        self._ensure_type("number", "integer", method_name="minimum")
        self._schema["minimum"] = n; return self
    def maximum(self, n: float) -> Self:
        self._ensure_type("number", "integer", method_name="maximum")
        self._schema["maximum"] = n; return self
    def multiple_of(self, n: float) -> Self:
        self._ensure_type("number", "integer", method_name="multiple_of")
        self._schema["multipleOf"] = n; return self

    def min_items(self, n: int) -> Self:
        self._ensure_type("array", method_name="min_items")
        self._schema["minItems"] = n; return self
    def max_items(self, n: int) -> Self:
        self._ensure_type("array", method_name="max_items")
        self._schema["maxItems"] = n; return self
    def unique_items(self, b: bool = True) -> Self:
        self._ensure_type("array", method_name="unique_items")
        self._schema["uniqueItems"] = b; return self

    # --- 4. Annotations ---
    def title(self, t: str) -> Self: self._schema["title"] = t; return self
    def description(self, d: str) -> Self: self._schema["description"] = d; return self
    def default(self, v: Any) -> Self: self._schema["default"] = v; return self
    def examples(self, ex: List[Any]) -> Self: self._schema["examples"] = list(ex); return self
    def enum(self, v: List[Any]) -> Self: self._schema["enum"] = list(v); return self
    def deprecated(self, b: bool = True) -> Self: self._schema["deprecated"] = b; return self

    # --- 5. Serialization & Persistence ---
    def end(self) -> Any:
        return self._parent if self._parent else self

    def build(self) -> Dict[str, Any]:
        return deepcopy(self._root_ref._schema)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.build(), indent=indent)

    def save(self, path: Path, indent: int = 2) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(indent=indent), encoding="utf-8")

class LogicalContext:
    def __init__(self, list_ref: List[Dict[str, Any]], parent: JSONSchemaBuilder, root: JSONSchemaBuilder) -> None:
        self._list, self._parent, self._root = list_ref, parent, root
    def add(self) -> JSONSchemaBuilder:
        new_s: Dict[str, Any] = {}
        self._list.append(new_s)
        return JSONSchemaBuilder(new_s, parent=self, root=self._root)
    def end(self) -> JSONSchemaBuilder:
        return self._parent