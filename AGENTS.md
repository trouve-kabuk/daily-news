# Project Guidance

Project-specific behavior and technical choices belong in `SPECS.md` and
`IMPLEMENTATION.md`. This file defines reusable engineering principles.

## Design Motto

Global order, local mess.

Prefer a small number of cohesive, medium-sized files over many tiny files
split by mechanical type. Files that change together should live together.

Group code by domain and pipeline step, not in generic `models`, `services`,
`adapters`, or `utils` packages. Concrete integrations belong near the
capability that owns them unless they become genuinely shared.

A file may contain its local types or schemas, prompts, processing functions,
and validation helpers. Split it only when it gains independent reasons to
change.

Packages own their public contracts. Composition roots translate between
neighboring package contracts rather than introducing a global domain-model
package.

## Architecture

Organize the system around its independent workflows and domain capabilities.
Common workflow examples are `offline` scheduled or manual work and `online`
interactive work, but use names that fit the project.

Workflow modules are composition roots: they coordinate use cases and translate
between capability contracts. Peer pipeline steps should not import one another
merely to advance the workflow.

Use shared lower-level modules only for concepts that genuinely cross
workflows. Examples include:

- `knowledge`: database access and canonical persistent records.
- `llm`: provider setup, model lifecycle, and shared inference helpers.
- `sql`: deterministic parsing, validation, and database-specific guardrails.
- `config`: application configuration.

Step-local models, prompts, and validation stay beside the step that uses them.
Only canonical persisted records or contracts genuinely shared across workflows
belong in a lower-level shared module.

Transports such as CLI and HTTP should be thin composition boundaries. They own
transport validation and rendering, not domain or workflow behavior.

## Import Boundaries

Import direction must follow architectural dependency direction. A typical
direction is:

```text
transport -> workflow -> capability -> lower-level shared modules
```

In particular:

```text
lower-level shared module -/-> workflow or transport
one independent workflow -/-> another independent workflow
capability -/-> composition root
```

Packages own the interfaces they require; concrete implementations satisfy
those interfaces without reversing the dependency direction. Shared modules
must not know workflow modules exist.

Avoid dependency loops. When two workflow steps use different representations,
their coordinator translates between their contracts. Promote a contract only
when it represents a genuinely shared or persistent concept, not merely to
avoid a small boundary translation.

## Work Organization

Keep the main application under `app/`. Mirror its domain boundaries in tests.

Keep these project files synchronized with relevant changes:

- `SPECS.md`: product behavior, scope, and invariants.
- `TODO.md`: user stories and functionality. Start every item with `[ ]`, `[x]`
  for completed work, or `[-]` for work on hold.
- `DEVLOG.md`: chronological implementation notes and discovered issues. Start
  every entry with a `YYYY-mm-dd HH:MM` timestamp.
- `IMPLEMENTATION.md`: technical choices, including both adopted and rejected
  approaches.
- `Dockerfile`: reproducible container build instructions.
