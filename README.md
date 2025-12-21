# python-opc-ua-models

Python dataclasses generated from OPC UA nodesets/specs for usage in MQTT app2app scenarios.

## TODO

* "basic 2"
  - generate a dataset per object with all variables
  - generate objects for all events
  - generate objects for all methods
* then "1" and "finish 2"
* is the basic "variable" object type the thing driving everything ? incl. triggering events?

---

* "3": https://github.com/pytransitions/transitions/blob/master/examples/Frequently%20asked%20questions.ipynb
* how to integrate the state machine in the app
* is it required to define also the persistance model? or only a callback mechanismto retrieve the data (most of the data model is already fixed by the ISA95... DataTypes)
* build dataset handler with key frame support


## Overall Design

To achieve a modern and standards based application architecture for manufacturing control apps the established and well-defined OPC UA companion specs are used as APIs for the shopfloor.

### Design Goals

* Manufacturing control/OT apps should be buildable like IT applications with cloud native/modern architecture principles
* Code is generated as much as possible from the OPC UA specs to lower the burden to adhere to the standards
* The tool approach MUST work for [ISA-95 JobControl](https://reference.opcfoundation.org/ISA95JOBCONTROL/v200/docs/) spec, SHOULD work also for others like Tightening, etc.

### Premises

* Event driven architecture via MQTT as transport protocol (no OPC UA Client/Server or Pub/Sub)
* OPC UA information models/companion specs are used for communication over MQTT
* Meta information is transported via MQTT user properties/cloud event headers to identify the message payload
* Implementations must only work via the generated dataclasses and not directly with the MQTT payloads (they are decoded/encoded in the background)
* There is an app UNS that has at least subtopics for `data` (variable publication/maybe setting), `events` and `invoke` (method calls)

### Model Generation Flow

#### 1. Generate `DataType` dataclasses from JSON Schemas

* Use/generate JSON schema from OPC UA XML (see OPC UA JSON Schemas below)
* Generate Python dataclasses via `datamodel-code-generator`

#### 2. Generate "API-level" dataclasses from nodeset

* define opt in list of object types relevant for class generation (e.g. ISA95JobOrderStatusEventType, ISA95JobResponseProviderObjectType, ISA95JobOrderReceiverObjectType)
* Generate dataclasses for events and methods using `opcua-asyncio` nodeset XML parser from `FreeOpcUa`
* ??????? Generate variables ... ??? what is the target structure for this!!!
* ...

Links:
* https://github.com/FreeOpcUa/opcua-asyncio/blob/master/asyncua/common/xmlparser.py

#### 3. Generate `StateMachine` from nodeset

### (??? 4. App Scaffolding/Demo App)

## OPC UA JSON Schemas

OPC UA NodeSets and base schemas can be found [here](https://github.com/OPCFoundation/UA-Nodeset).

### OPC UA Base Schema

Although OPC UA is all XML/XSD you can find the basic schema already as JSON Schema in the repo [here](https://github.com/OPCFoundation/UA-Nodeset/blob/latest/Schema/opc.ua.jsonschema.json).

### Other Specs / OPC UA Model Compiler

The schema for ISA-95 JobControl can be generated with the `UA-ModelCompiler`. The tool is on the OPC Foundation github and can be run from a container.

#### Pull latest docker image from https://github.com/OPCFoundation/UA-ModelCompiler:

```
docker pull ghcr.io/opcfoundation/ua-modelcompiler:preview
```

#### Run model generation for specified uri(s) (multiple possible at once):

```
docker run -v "${PWD}/UA-Nodeset":/nodesets -v "${PWD}/UA-Generated":/nodesets/generated --rm ghcr.io/opcfoundation/ua-modelcompiler:preview compile-nodesets -input /nodesets -o2 /generated -uri http://opcfoundation.org/UA/ISA95-JOBCONTROL_V2/
```

#### Required Workarounds

1. UA schema: $ID missing
1. ISA95: fix $ID & remove object & fix refs to UA base spec

## Python Libs

* https://github.com/FreeOpcUa/opcua-asyncio/
* https://github.com/koxudaxi/datamodel-code-generator
* https://github.com/Fatal1ty/mashumaro

## Notes

### ISA-95 vs. ISA-88

* https://iacsengineering.com/isa-88-and-isa-95-integrated-consulting/
* https://mdcplus.fi/blog/what-is-isa-88-manufacturing/
* https://www.isa.org/products/isa-tr88-95-01-2008-using-isa-88-and-isa-95-togeth

### Links

* https://json-schema.org/draft/2020-12/json-schema-core
* https://www.unified-automation.com/de/produkte/entwicklerwerkzeuge/uaexpert.html
* https://prosysopc.com/products/opc-ua-browser/

### Add submodule for nodeset repo

https://miroslav-slapka.medium.com/handle-git-submodules-with-ease-55621afdb7bb

```
git submodule add -b latest https://github.com/OPCFoundation/UA-Nodeset.git
```
