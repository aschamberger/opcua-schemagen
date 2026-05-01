# opcua-schemagen

Generate JSON Schema from OPC UA nodesets/companion specifications for MQTT app-to-app scenarios.

The repository, Python package, and CLI are named `opcua-schemagen`. The import package is `opcua_schemagen`.

## Requirements

* Python >= 3.14
* [uv](https://docs.astral.sh/uv/)

## Setup

```
git submodule update --init
uv sync
```

## Usage

All commands operate on a spec path relative to `UA-Nodeset/`.

```
# List all available nodesets
uv run opcua-schemagen index

# Show nodeset metadata (namespaces, required models, aliases)
uv run opcua-schemagen info ISA95-JOBCONTROL

# List UAObjectType nodes and their children
uv run opcua-schemagen objects ISA95-JOBCONTROL

# List UADataType nodes
uv run opcua-schemagen types ISA95-JOBCONTROL

# Generate JSON Schema (main command)
uv run opcua-schemagen appschema Machinery/Jobs machinery_jobs.schema.json --nodeid-replace "ns=2;i=1002->ns=2;i=1008"

# Include all HasAddIn objects (e.g. Identification, Monitoring, LifetimeCounters, ...)
uv run opcua-schemagen appschema IJT/Tightening ijt_tightening.schema.json --include-objects IJT/Base --include-all-addins

# Include only specific addin types by their TypeDefinition display name
uv run opcua-schemagen appschema IJT/Tightening ijt_tightening.schema.json --include-objects IJT/Base \
  --include-addin MachineryItemIdentificationType \
  --include-addin MonitoringType

# Inject tightening-specific interface into the generic tool interface slot (cross-namespace mixin)
uv run opcua-schemagen appschema IJT/Tightening ijt_tightening.schema.json --include-objects IJT/Base --include-all-addins \
  --interface-replace "IJT/Base|ns=1;i=1004->IJT/Tightening|ns=1;i=1003"
```

> Note: `--nodeid-replace` is needed to activate sub state machines when node IDs require patching.
> Note: `--include-objects` includes objects/methods/events from a parent spec that the main spec extends but doesn't redefine.
> Note: `--include-all-addins` includes all objects referenced via `HasAddIn` from every processed `UAObjectType`. Use `--include-addin <TypeName>` (repeatable) to include only addins whose `HasTypeDefinition` display name matches.
> Note: `--interface-replace` handles the OPC UA interface-injection/mixin pattern for cross-namespace substitution. When an object has `HasInterface → <from type>`, the schema generator resolves type-hierarchy variables from `<to type>` instead. The format is `SPEC/PATH|ns=1;i=<from_id>->SPEC/PATH|ns=1;i=<to_id>` where spec paths are relative to `UA-Nodeset/` and node IDs are the local IDs as defined within each respective nodeset.

## Overall Design

To achieve a modern and standards based application architecture for manufacturing control apps the established and well-defined OPC UA companion specs are used as APIs for the shopfloor.

### Design Goals

* Manufacturing control/OT apps should be buildable like IT applications with cloud native/modern architecture principles
* Code is generated as much as possible from the OPC UA specs to lower the burden to adhere to the standards
* The tool approach MUST work for [OPC 40001-3: Machinery Job Mgmt](https://reference.opcfoundation.org/Machinery/Jobs/v100/docs/)/[OPC 10031-4: ISA-95-4 Job Control](https://reference.opcfoundation.org/ISA95JOBCONTROL/v200/docs/) spec, SHOULD work also for others like Tightening, etc.

### JSON Schema Generation

#### Overall Flow

```mermaid
flowchart TD
    A["NodeSet2.xml<br>(main spec)"] -->|parse| B["Import nodeset<br>+ resolve dependencies"]
    A2["NodeSet2.xml<br>(--include-objects)"] -.->|parse| B
    B --> C["Build URI ↔ file mapping<br>for all UA-Nodeset/ specs"]
    C --> D["Recursively import<br>all RequiredModels"]
    D --> E[Own DataTypes]
    E --> F["Parent/dependency<br>DataTypes"]
    F --> G["Included namespace<br>Objects / Methods / Events"]
    G --> H["Own namespace<br>Objects / Methods / Events"]
    H --> HA["HasAddIn objects<br>(--include-all-addins / --include-addin)"]
    HA --> I[Compose root anyOf]
    I --> J["JSON Schema<br>(.schema.json)"]

    subgraph "Per UAObjectType"
        direction TB
        H1["Resolve type hierarchy<br>variables"] --> H1B["Inject interface variables<br>(--interface-replace)"]
        H1B --> H2[Add own variables]
        H2 --> H3["Resolve type hierarchy<br>methods → Call/Response"]
        H3 --> H4["Resolve type hierarchy<br>events"]
        H4 --> H5[Extract state machine]
    end

    H --> H1
```

#### Details

* Nodeset parsing via `opcua-asyncio` XMLParser from `FreeOpcUa`
* All `RequiredModel` entries in a nodeset are recursively imported, so the full dependency tree is available (e.g. DI, Machinery, IA, Machinery/Result for IJT)
* Generate datatypes from nodeset(s) including base UADataTypes
* Generate a 'DataSet' for every UAObjectType with all variables added from the type hierarchy
* Generate 'Method' for every Input-/OutputArgument per UAMethod (split into Call/Response pairs)
* Generate 'Event' for every UAEvent referenced via `GeneratesEvent`
* OPC UA placeholder variables (`OptionalPlaceholder`, `MandatoryPlaceholder` modelling rules) are skipped — these are template slots, not concrete properties
* When `--interface-replace` is used, type-hierarchy variables from the replacement interface type are merged into the DataSet for every `UAObjectType` child-object that carries a matching `HasInterface` reference — modelling the OPC UA interface-injection/mixin pattern across namespace boundaries
* root `anyOf` contains all non-datatype objects (DataSets, Methods, Events)
* Custom attributes:
  * `x-opc-ua-type` to distinguish: "DataSet", "Method", "Event"
  * `x-opc-ua-state-machine` with extracted state machine states and transitions
  * `x-cloudevent-type` / `x-cloudevent-dataschema` for CloudEvents integration

#### Inheritance / Processing Order

* Own DataTypes are processed first and take precedence over parent definitions (first-wins via duplicate guard)
* Parent/dependency DataTypes fill in anything the own namespace didn't define
* When `--include-objects` is used, included namespace objects are processed before own namespace objects, so the own spec can override parent properties
* When `--include-all-addins` or `--include-addin` is used, `HasAddIn` child objects of each processed `UAObjectType` are included as additional DataSet definitions; their variables, methods, and events are resolved the same way as regular component objects. `--include-addin` filters by the display name of the addin's `HasTypeDefinition` type node
* When `--interface-replace` is used, for every `UAObjectType` whose local node ID matches the *from* side, the type-hierarchy variables of the *to* type are resolved and merged before the object's own variables are added — allowing spec-specific variable sets to override generic interface placeholders
* Type hierarchy is walked recursively for variables, methods, events, and state machines — parent types contribute their members, child types overwrite

#### Why `--include-objects`?

Some OPC UA companion specs split their model across multiple nodesets. For example, IJT/Tightening only defines a single abstract interface (`ITighteningToolParametersType`) while all concrete objects (JoiningSystemType, health, maintenance, results, asset management) live in IJT/Base. Without `--include-objects IJT/Base`, the generated schema would contain only DataTypes and no usable objects. Auto-detecting which dependencies are "domain parents" vs "infrastructure" (DI, Machinery) is not reliable, so inclusion is explicit.

#### Why `--include-all-addins` / `--include-addin`?

OPC UA uses `HasAddIn` references to attach optional building-block objects to a type — things like `Identification`, `Monitoring`, `LifetimeCounters`, `OperationCounters`, `Notifications`, `Maintenance`. These are defined as children of the `UAObjectType` in the nodeset, but they are architecturally separate concerns from the type's core variables: they come with their own variables, methods, and events and are meant to be included (or not) depending on what a given device supports.

For event-based MQTT usage these building blocks are relevant payloads in their own right. `--include-all-addins` causes every `HasAddIn` child of every processed `UAObjectType` to be emitted as its own DataSet definition, with its full variable/method/event hierarchy resolved identically to regular component objects. `--include-addin <TypeName>` is the selective form — only addins whose `HasTypeDefinition` display name matches are included, which is useful when only a subset of building blocks is relevant or supported.

#### Why `--interface-replace`?

OPC UA allows interface types (`BaseInterfaceType` subtypes) to act as extension slots on object type children. A base spec (e.g. IJT/Base) models a generic placeholder object (e.g. `<Tool>`) with `HasInterface → IToolType`, meaning the tool parameters are defined by whoever implements the spec. A specialising companion spec (e.g. IJT/Tightening) then defines a concrete replacement interface (`ITighteningToolParametersType`) that adds domain-specific variables (torque limits, drive method, etc.).

In a classic OPC UA server this substitution happens at instantiation time. For event-based MQTT usage the generated schema must reflect the actual concrete variables. `--interface-replace` bridges this gap by instructing the schema generator to resolve variables from the replacement interface type wherever the generic interface is referenced, without requiring the base nodeset to be modified.

The format is `SPEC/PATH|ns=1;i=<from_id>->SPEC/PATH|ns=1;i=<to_id>` where both spec paths are relative to `UA-Nodeset/` and node IDs are the *local* IDs as defined within each respective nodeset (i.e. `ns=1` always refers to the own namespace of that file).

Links:
* https://github.com/FreeOpcUa/opcua-asyncio/blob/master/asyncua/common/xmlparser.py

### Annoyances

* Nodesets overall:
  * do not contain the spec's title
* Machinery JobMgmt Nodeset:
  * does not contain method's return status
  * does not contain the full state machine
* IJT Tightening Nodeset:
  * concrete objects live entirely in IJT/Base, Tightening only adds one abstract interface — requires `--include-objects` to produce a useful schema
  * addin objects (Identification, LifetimeCounters, Monitoring, Notifications, OperationCounters) are defined via `HasAddIn` references in IJT/Base — requires `--include-all-addins` (or targeted `--include-addin`) to include them in the schema
  * `IToolType` in IJT/Base defines a generic `Parameters` placeholder; `ITighteningToolParametersType` in IJT/Tightening injects tightening-specific variables (DesignType, DriveMethod, DriveType, MaxSpeed, MaxTorque, MinTorque, MotorType, ShutOffMethod) — requires `--interface-replace "IJT/Base|ns=1;i=1004->IJT/Tightening|ns=1;i=1003"` to include them in the schema

## Python Libs

* https://github.com/fastapi/typer
* https://github.com/FreeOpcUa/opcua-asyncio/
* https://github.com/Textualize/rich
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
