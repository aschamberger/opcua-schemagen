# python-opc-ua-models

Python dataclasses generated from OPC UA nodesets/specs for usage in MQTT app2app scenarios.

## Usage

```
uv run ./scripts/opcmodelgen.py nodeset appschema Machinery/Jobs machinery_jobs.jsonschema.json
uv run ./scripts/opcmodelgen.py schema dataclasses machinery_jobs.jsonschema.json
```

## TODO

* dataclasses:generate
* generate state machine:
  * this lib?! https://github.com/pytransitions/transitions/blob/master/examples/Frequently%20asked%20questions.ipynb
  * states can be put in JSON schema into variable from "FiniteStateMachine"
* test with another nodeset: tightening?

* ???: how to handle spec versions schemas? $id with versions? how to match versions of files in OPC UA nodeset repo?
* ???: what can be upstreamed to asyncua from wrapper class?

## Overall Design

To achieve a modern and standards based application architecture for manufacturing control apps the established and well-defined OPC UA companion specs are used as APIs for the shopfloor.

### Design Goals

* Manufacturing control/OT apps should be buildable like IT applications with cloud native/modern architecture principles
* Code is generated as much as possible from the OPC UA specs to lower the burden to adhere to the standards
* The tool approach MUST work for [OPC 40001-3: Machinery Job Mgmt](https://reference.opcfoundation.org/Machinery/Jobs/v100/docs/)/[OPC 10031-4: ISA-95-4 Job Control](https://reference.opcfoundation.org/ISA95JOBCONTROL/v200/docs/) spec, SHOULD work also for others like Tightening, etc.

### Model Generation Flow

#### 1. Generate JSON Schema from OPC UA nodeset XML files

* Nodeset parsing via `opcua-asyncio` XMLParser from `FreeOpcUa`
* Generate datatypes from nodeset(s) including base UADataTypes
* Generate a 'DataSet' for every OPC UAObjectType with all variables added from the type hierarchie
* Generate 'Method' for every Input-/OutputArgument per UAMethod
* Generate 'Event' for every UAEvent
* root allOf contains all non-datatype objects
* custom attribute "x-opc-ua-type" to distinguish: "DataSet", "Method", "Event"

Links:
* https://github.com/FreeOpcUa/opcua-asyncio/blob/master/asyncua/common/xmlparser.py

#### 2. Generate dataclasses from JSON Schema

* Generate Python dataclasses via `datamodel-code-generator`

#### 3. Generate `StateMachine` from nodeset

<TODO>

## Python Libs

* https://github.com/fastapi/typer
* https://github.com/FreeOpcUa/opcua-asyncio/
* https://github.com/koxudaxi/datamodel-code-generator

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
