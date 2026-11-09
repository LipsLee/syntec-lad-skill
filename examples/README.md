# Examples

`demo.plan.json` is a self-contained, reviewable `syntec-lad-plan/v1`
example. It intentionally uses placeholder I/O addresses and a timer preset;
they are not a recommendation for a real machine.

```powershell
python -m syntec_lad build examples\demo.plan.json examples\demo.lad
python -m syntec_lad inspect examples\demo.lad
python -m syntec_lad validate examples\demo.lad
```

`mvp.plan.json` / `mvp.lad` add parallel branches, closed contacts, a timer,
counter, register comparison, data transfer, and a subprogram call. Both files
use placeholder device numbers and are intended for format/verification tests,
not as machine-ready logic.

```powershell
python -m syntec_lad build examples\mvp.plan.json examples\mvp.lad
python -m syntec_lad inspect examples\mvp.lad
python -m syntec_lad validate examples\mvp.lad
```

The checked-in `.lad` fixtures pass both the SDK parser and the native LadDLL
syntax checker when the installed LadEditor is present.
