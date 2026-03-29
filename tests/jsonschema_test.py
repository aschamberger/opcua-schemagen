import json

import pytest

from opcua_schemagen.jsonschema import JSONSchemaBuilder, SchemaValidationError


@pytest.fixture
def builder():
    """Provides a fresh root builder for each test."""
    return JSONSchemaBuilder.start()


# --- 1. Root & Structural Tests ---
def test_root_id(builder):
    builder.id("https://example.com/schema.json")
    assert builder.build()["$id"] == "https://example.com/schema.json"


def test_lazy_root_initialization(builder):
    # Should be empty (except for $schema) initially
    assert "type" not in builder.build()
    # Should become object upon adding a prop
    builder.prop("name").string().end()
    assert builder.build()["type"] == "object"


def test_required_props(builder):
    builder.prop("id").integer().end().required_props(["id"])
    schema = builder.build()
    assert "id" in schema["required"]
    assert schema["type"] == "object"


# --- 2. Isolation & Logic Tests ---
def test_logical_branch_isolation(builder):
    """Verifies that branches in anyOf do not leak data to each other."""
    schema = (
        builder.any_of()
        .add()
        .object()
        .prop("branch_a")
        .string()
        .end()
        .end()
        .add()
        .object()
        .prop("branch_b")
        .integer()
        .end()
        .end()
        .end()
        .build()
    )

    branch_a = schema["anyOf"][0]
    branch_b = schema["anyOf"][1]

    assert "branch_a" in branch_a["properties"]
    assert "branch_a" not in branch_b["properties"]
    assert "branch_b" in branch_b["properties"]


def test_nested_logic_isolation(builder):
    """Checks isolation between $defs anyOf and root anyOf."""
    builder.definition("Shared").any_of().add().const(1).end().end().end()
    builder.any_of().add().const(2).end().end()

    schema = builder.build()
    assert schema["$defs"]["Shared"]["anyOf"][0]["const"] == 1
    assert schema["anyOf"][0]["const"] == 2
    # Ensure no crosstalk
    assert len(schema["$defs"]["Shared"]["anyOf"]) == 1
    assert len(schema["anyOf"]) == 1


# --- 3. Constraints Tests ---
def test_string_constraints(builder):
    builder.prop("email").string().min_length(5).max_length(50).pattern("@").format(
        "email"
    ).end()
    s = builder.build()["properties"]["email"]
    assert s["minLength"] == 5
    assert s["maxLength"] == 50
    assert s["pattern"] == "@"
    assert s["format"] == "email"


def test_number_constraints(builder):
    builder.prop("count").integer().minimum(0).maximum(10).multiple_of(2).end()
    n = builder.build()["properties"]["count"]
    assert n["minimum"] == 0
    assert n["maximum"] == 10
    assert n["multipleOf"] == 2


def test_array_constraints(builder):
    builder.prop("items").array().min_items(1).max_items(
        3
    ).unique_items().string().end().end()
    a = builder.build()["properties"]["items"]["items"]
    assert a["minItems"] == 1
    assert a["maxItems"] == 3
    assert a["uniqueItems"] is True


def test_nullable(builder):
    builder.prop("bio").string().nullable().end()
    assert builder.build()["properties"]["bio"]["type"] == ["string", "null"]


# --- 4. Topological Sort Tests ---
def test_topological_sort(builder):
    """User depends on Address. Address should be sorted first in $defs."""
    builder.definition("User").object().prop("loc").ref("Address").end().end()
    builder.definition("Address").object().prop("city").string().end().end()

    schema = builder.build()
    defs_keys = list(schema["$defs"].keys())

    assert defs_keys.index("Address") < defs_keys.index("User")


def test_circular_dependency_handling(builder):
    """Recursive refs are allowed in 2020-12; build() should not crash."""
    builder.definition("Node").object().prop("next").ref("Node").end().end()
    schema = builder.build()
    assert "Node" in schema["$defs"]


# --- 5. Guardrail & Error Tests ---
def test_schema_validation_error(builder):
    """Ensure we cannot put string constraints on an integer."""
    with pytest.raises(SchemaValidationError):
        builder.prop("age").integer().min_length(2)


def test_invalid_attribute_access(builder):
    with pytest.raises(AttributeError):
        builder.non_existent_method()


# --- 6. Vendor & Persistence Tests ---
def test_vendor_extensions(builder):
    builder.prop("field").x_meta_data("val").end()
    assert builder.build()["properties"]["field"]["x-meta-data"] == "val"


def test_persistence_io(builder, tmp_path):
    path = tmp_path / "schema.json"
    builder.title("Test").save(path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["title"] == "Test"
