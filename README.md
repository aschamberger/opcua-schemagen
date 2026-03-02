# OPC UA nodesets to JSON Schema

JSON Schema generated from OPC UA nodesets/companion specs for usage in MQTT app2app scenarios.

## Usage

```
uv run ./scripts/schemagen.py appschema Machinery/Jobs machinery_jobs.jsonschema.json --nodeid-replace "ns=2;i=1002->ns=2;i=1008"
uv run ./scripts/schemagen.py appschema IJT/Tightening ijt_tightening.jsonschema.json
```
node ids need to be replaced to active the sub statemachines

## TODO

* schema file per nodeset file?
* what can be upstreamed to asyncua from wrapper class?
* test with another nodeset: tightening?

## Overall Design

To achieve a modern and standards based application architecture for manufacturing control apps the established and well-defined OPC UA companion specs are used as APIs for the shopfloor.

### Design Goals

* Manufacturing control/OT apps should be buildable like IT applications with cloud native/modern architecture principles
* Code is generated as much as possible from the OPC UA specs to lower the burden to adhere to the standards
* The tool approach MUST work for [OPC 40001-3: Machinery Job Mgmt](https://reference.opcfoundation.org/Machinery/Jobs/v100/docs/)/[OPC 10031-4: ISA-95-4 Job Control](https://reference.opcfoundation.org/ISA95JOBCONTROL/v200/docs/) spec, SHOULD work also for others like Tightening, etc.

### Model Generation Flow

1. Generate JSON Schema from OPC UA nodeset XML files
2. Generate dataclasses from JSON Schema
3. Manual additions for missing things or additions (e.g. full state machine in Machinery Job Management)

### JSON Schema Generation

* Nodeset parsing via `opcua-asyncio` XMLParser from `FreeOpcUa`
* Generate datatypes from nodeset(s) including base UADataTypes
* Generate a 'DataSet' for every OPC UAObjectType with all variables added from the type hierarchie
* Generate 'Method' for every Input-/OutputArgument per UAMethod
* Generate 'Event' for every UAEvent
* root allOf contains all non-datatype objects
* custom attribute "x-opc-ua-type" to distinguish: "DataSet", "Method", "Event"
* custom attribute "x-opc-ua-state-machine" with extracted state machine

Links:
* https://github.com/FreeOpcUa/opcua-asyncio/blob/master/asyncua/common/xmlparser.py

### Annoyances

* Nodesets overall:
  * do not contain the spec's title
* Machinery JobMgmt Nodeset:
  * does not contain method's return status
  * does not contain the full state machine

## Python Libs

* https://github.com/fastapi/typer
* https://github.com/FreeOpcUa/opcua-asyncio/

## Notes

### Links

* https://json-schema.org/learn // https://json-schema.org/draft/2020-12/json-schema-core
* https://www.unified-automation.com/de/produkte/entwicklerwerkzeuge/uaexpert.html
* https://prosysopc.com/products/opc-ua-browser/
* https://dev.to/somedood/bitmasks-a-very-esoteric-and-impractical-way-of-managing-booleans-1hlf

### uv

```
uv lock --upgrade
```

### Add submodule for nodeset repo

https://miroslav-slapka.medium.com/handle-git-submodules-with-ease-55621afdb7bb

```
git submodule add -b latest https://github.com/OPCFoundation/UA-Nodeset.git
```
