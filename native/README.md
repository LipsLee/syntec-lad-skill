# Native syntax-check helper

`LadDLL.dll` is a 32-bit native DLL. Build the helper with an x86 .NET
Framework compiler:

```powershell
& 'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe' `
  /nologo /target:exe /platform:x86 `
  /out:native\bin\LadSyntaxCheck.exe native\LadSyntaxCheck.cs
```

Run it directly for diagnostics. Set `LAD_DLL` to the `LadDLL.dll` installed
with your own LadEditor copy:

```powershell
$LAD_DLL = 'C:\path\to\LadEditor\LadDLL.dll'
native\bin\LadSyntaxCheck.exe .\file.lad $LAD_DLL
```

The helper calls the ordinal exports `#121` (`SyntaxCheck`), `#102`
(`GetErrorCount`), `#100` (`GetErrorLine`), and `#124` (`GetSyntaxErrType`).
It sets the DLL's local private-key state using the formula recovered from
`MLCEdit.mainForm.LoadFile` before checking. Exit code 0 means
`syntax_ret == 1` and `error_count == 0`.
