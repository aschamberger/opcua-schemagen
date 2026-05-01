# opcua-schemagen — Copilot Instructions

## Project Overview

Generates JSON Schema from OPC UA NodeSet2.xml companion specifications for MQTT app-to-app scenarios.

- CLI: `opcua-schemagen` (via `uv run opcua-schemagen`)
- Import package: `opcua_schemagen`
- Source: `src/opcua_schemagen/`
- Generated schemas: `schemas/`

## Setup

```
git submodule update --init
uv sync
```

## Regenerating Schemas

The JSON files in `schemas/` are generated — do not edit them manually, regenerate instead.

**Machinery Jobs:**
```
uv run opcua-schemagen appschema Machinery/Jobs machinery_jobs.schema.json --nodeid-replace "ns=2;i=1002->ns=2;i=1008"
```

**IJT Tightening:**
```
uv run opcua-schemagen appschema IJT/Tightening ijt_tightening.schema.json --include-objects IJT/Base --include-all-addins \
  --interface-replace "IJT/Base|ns=1;i=1004->IJT/Tightening|ns=1;i=1003"
```

## Custom Schema Attributes

- `x-opc-ua-type`: `"DataSet"` | `"Method"` | `"Event"`
- `x-opc-ua-state-machine`: extracted state machine states and transitions
- `x-type-id`: reverse-DNS type identifier (e.g. `org.opcfoundation.IJT.Base.JoiningResultDataType.v1`)
- `x-type-schema`: schema URI for this definition (e.g. `https://aschamberger.github.com/schemas/UA/IJT/Base/v1.01.0/#/$defs/JoiningResultDataType`)

## Key Source Files

- `ns2js.py` — `NodesetToJSONSchema`: core schema generation logic
- `schemagen.py` — CLI entry point (typer)
- `xmlparser.py` — `WrappedXMLParser`: NodeSet2.xml parsing wrapper
- `jsonschema.py` — `JSONSchemaBuilder`: fluent schema construction
