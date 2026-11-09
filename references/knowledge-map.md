# Engineering Knowledge Map

The skill has two layers:

- **Native tool layer:** inspect, build, patch, remap, diff, and natively
  validate `.lad` files.
- **Engineering knowledge layer:** address semantics, process patterns,
  subsystem recipes, field conventions, and anti-patterns.

Use the smallest reference that answers the current question. Do not load the
entire knowledge base for a simple record inspection.

## Route by task

| Task | Read first |
|---|---|
| Pick or verify an address | `panming/address-map.md` |
| Look up a known C/S/R address | run `syntec-lad lookup <address>` or read `panming/address-index.md` |
| Write a contact, coil, timer, counter, compare, or flow block | `panming/element-grammar.md` |
| Design a common circuit | `panming/patterns.md` |
| Implement lubrication, hydraulic, chip conveyor, coolant, tool magazine, or M code | `panming/function-library.md` |
| Follow real-machine layout and naming conventions | `panming/project-conventions.md` |
| Encode or check Chinese comments | `panming/comment-encoding.md` |
| Inspect raw record layout | `panming/lad-format.md` |
| Resolve R512~R1023 permissions | `panming/R512-R1023-权限表.md` |
| Understand opcodes or round-trip structure | `panming/TECHNICAL.md` |
| Modify an existing file safely | `workflow.md` |

## Required order before writing

1. Run `inspect` on the native file. Do not infer record boundaries.
2. Check the address class and write authority in `panming/address-map.md`.
3. If the request matches a known circuit, read the relevant pattern or
   function-library section.
4. Ask for every missing I/O point, register, timer, counter, preset, and
   interlock relationship.
5. Draft a reviewed JSON plan/change.
6. Build or patch a new output and validate it with the native SDK.

## Source hierarchy

When two statements conflict, use this order:

1. Native `LadDLL.SyntaxCheck` and parser readback for what the editor accepts.
2. The official Syntec manual/interface page for address meaning and
   permissions.
3. `panming` documents marked as manual/specification.
4. `panming` documents marked as editor/real-machine observation.
5. Rules explicitly marked as inference or single-machine habit.

Never promote a lower-level observation into an editor rule without evidence.

## Address notation

The upstream documents use short forms such as `I0`, `C6`, `R81`, and
`R600.1`. The SDK accepts those forms but displays canonical padded forms such
as `I000`, `C006`, `R0081`, and `R0600.01`. See `address-notation.md` before
copying examples into a plan.

The upstream snippets also use the upstream tool's `add_rung` element objects.
They are not this SDK's plan schema. Translate the circuit into
`syntec-lad-plan/v1` or `syntec-lad-change/v1`; use the schemas under
`syntec_lad/schema/` as the source of truth.

## Safety boundary

The knowledge layer is not a license to invent values. It can suggest a
pattern and explain an address class, but the actual I/O point, register,
timer, counter, preset, and interlock must come from the user or the reviewed
point table.
