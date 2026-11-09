# Address Notation Adapter

The upstream `panming` documents use short address forms. The SDK accepts
short forms and canonical padded forms, but its inspection output uses a
uniform canonical display. Use this page when copying an upstream example into
a JSON plan or change document.

## Accepted grammar

| Family | SDK input | Canonical display | Limit |
|---|---|---|---|
| I / O / A / C / S | `I0`, `I000`, `C6`, `C006` | `I000`, `C006` | 0..511 |
| R register | `R81`, `R0081` | `R0081` | 0..65535 |
| R bit | `R600.1`, `R0600.01` | `R0600.01` | register 0..639, bit 0..31 |
| Timer | `TM0`, `TM000` | `TM000` | 0..255 |
| Counter | `CT0`, `CT000` | `CT000` | 0..255 |
| Constant | `#7`, `#00007` | `#00007` | signed 16-bit form supported by the compiler |

## Common conversions

| Upstream example | SDK canonical | Note |
|---|---|---|
| `I0` | `I000` | physical input |
| `O10` | `O010` | physical output |
| `A0` | `A000` | internal auxiliary flag |
| `C6` | `C006` | PLC-to-CNC command |
| `S29` | `S029` | CNC-to-PLC status, read-only |
| `R81` | `R0081` | parameter register; do not write from the ladder |
| `R193.1` | `R0193.01` | retained data bit |
| `R600.1` | `R0600.01` | bit-addressed R area |
| `TM0` | `TM000` | timer |
| `CT0` | `CT000` | counter |

## Important boundaries

- `S` addresses are read-only in the ladder. Never generate a coil, set, or
  reset targeting `S`.
- `C` addresses are commands to the CNC, not general-purpose flags. Use `A`
  for internal flags.
- `R81`~`R100` are the MLC parameter window. The ladder reads them; the
  operator changes the corresponding `3401`~`3420` parameters.
- `R640` and above cannot be bit-addressed according to the upstream
  permission table. Use `A` for internal bits.
- The SDK parser accepts short forms for convenience. The reviewed plan should
  still use the exact address family and number from the user's point table.
