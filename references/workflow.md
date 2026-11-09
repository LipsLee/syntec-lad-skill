# Syntec `.lad` 修改工作流

本参考用于需要修改现有梯形图、生成新梯形图、批量重映射地址或排查原生
校验失败时。Skill 的强制安全规则仍以 `SKILL.md` 为准。

## 0. 写计划前先过知识门

不要凭记忆猜地址类别或工艺结构。先按任务读取最小必要的知识文件：

- 地址类别、权限、C/S/R 含义：`panming/address-map.md`
- 精确地址：`python $LadCli lookup C6` 或
  `panming/address-index.md`
- 元件语法：`panming/element-grammar.md`
- 常用回路：`panming/patterns.md`
- 润滑/液压/排屑/刀库/M 码：`panming/function-library.md`
- 现场约定和注释：`panming/project-conventions.md`
- 短地址与 SDK 规范地址：`address-notation.md`

如果知识文件给出的是真机示例，只复用结构，不复用它的具体 I/O、寄存器、
定时器、预置值或互锁关系。缺失值必须向用户确认。

## 1. 只处理原生 `.lad`

`.lad` 是二进制记录容器。不要用文本编辑器直接修改，也不要把 `.il`、PDF、
BMP 或编辑器截图当作生成输入。已有文件必须先读取真实记录边界和哈希，
不能根据截图行号猜测。

## 2. 先检查

```powershell
python $LadCli inspect .\source.lad --json --output .\source.inspect.json
```

确认以下字段：

- `source.sha256`：后续 `syntec-lad-change/v1` 的 `base_sha256`
- `source.encoding`：`gb2312`、`gb18030` 或 `big5`
- 程序边界与子程序调用关系
- 每个 network candidate 的 `record_start`、`record_end`、`sha256`、`preview`
- 目标网络之前的注释记录，以及是否跨物理记录
- `integrity.issues` 是否为空

`inspect --output` 不得指向输入 `.lad`，SDK 会拒绝这种覆盖。

需要独立交叉检查时，还可以直接运行 `tools/lad_parser.py`：

```powershell
python .\tools\lad_parser.py .\source.lad --no-write
```

该解析器只读，不会修改 `.lad`，适合比较记录数、注释数和摘要哈希。

## 3. 新建文件：`syntec-lad-plan/v1`

适用场景：从零生成主程序或普通子程序，不修改现有文件。

顶层字段：

```json
{
  "schema": "syntec-lad-plan/v1",
  "encoding": "gb2312",
  "variables": [],
  "programs": []
}
```

`programs` 至少包含一项。`MAIN` 是主程序；普通子程序名必须为 1 到 6 位
字母或数字。每个 network 必须包含 `actions`，可选 `condition` 和
`comment`。

支持的条件形态：

- `{"contact": "I000"}`：常开触点
- `{"contact": "I001", "normally": "closed"}`：常闭触点
- `{"all": [condition, ...]}`：串联
- `{"any": [condition, ...]}`：并联
- `{"always": true}`：无条件
- `{"compare": {"relation": "=", "value": "R0001", "other": "#00005"}}`

支持的普通动作：

- 线圈：`OUT`、`OUTI`、`PLS`、`PLF`、`SET`、`RST`
- 定时器：`TIM`，必须提供 `timer`、`unit`、`preset`
- 计数器：`CNTUP`、`CNTDN`、`CNTRESET`，必须提供 `counter` 和适用的
  `preset`
- 流程：`JMP`、`JSR`、`MSG`
- 数据：`MOV`、`ADD`、`SUB`、`MUL`、`DIV`、`AND`、`OR`、`XOR`

`LAB`、`RTS`、`END` 由程序结构生成，不应作为普通动作手工拼接。`JMP` 和
`JSR` 的目标必须是已声明的普通子程序名；不调用 `MAIN` 或未声明的局部
`LAB`。`CNTRESET` 不接受非零预置值。

格式样例见 `examples/demo.plan.json` 和 `examples/mvp.plan.json`。其中的
地址和预置值全部是占位示例。

生成并校验：

```powershell
python $LadCli build .\plan.json .\generated.lad
```

只有 Python 回读和 LadDLL 原生校验都通过后，命令才会输出成功状态。

## 4. 修改现有文件：`syntec-lad-change/v1`

适用场景：围绕已检查出的完整网络记录范围做插入、替换或删除。

顶层字段：

```json
{
  "schema": "syntec-lad-change/v1",
  "base_sha256": "<source.sha256>",
  "operations": []
}
```

每个操作都必须带目标范围的 `expected_sha256`：

```json
{
  "op": "replace",
  "record_start": 19,
  "record_end": 26,
  "expected_sha256": "<candidate.sha256>",
  "networks": [
    {
      "condition": {"contact": "I010"},
      "actions": [{"op": "OUT", "operand": "O010"}]
    }
  ]
}
```

支持的操作：

- `insert`：按 `at_record` 插入，或按 `record_start`/`record_end` 加
  `position: before|after` 插入
- `replace`：替换完整记录范围
- `delete`：删除完整记录范围

应用：

```powershell
python $LadCli patch .\source.lad .\change.json .\patched.lad
```

SDK 会拒绝以下情况：

- `base_sha256` 或 `expected_sha256` 过期
- 目标跨网络/注释边界
- 目标包含 `LAB`、`RTS`、`END`、`FEND` 等结构行
- 多个操作范围重叠
- 只选中跨多个物理记录的注释块的一部分

成功后只会改变目标记录，未修改记录保持原始字节。

## 5. 批量重映射地址

当用户提供明确的源到目标映射时：

```powershell
python $LadCli remap .\source.lad .\mapped.lad `
  --mapping-file .\mapping.json `
  --expected-sha256 <source.sha256> `
  --report .\mapped.report.json
```

`mapping.json` 是扁平 JSON 对象：

```json
{
  "I000": "I400",
  "O010": "R330.00"
}
```

重映射是同步替换，不会发生 `I000 -> I400 -> R999` 的级联。加
`--require-all-sources` 可要求每个源地址都实际出现在梯形图操作数中。
执行前仍须确认用户给出的目标地址合法，SDK 不会替用户选择地址。

## 6. 交付检查

独立的解析校验：

```powershell
python $LadCli validate .\output.lad
```

差异检查：

```powershell
python $LadCli diff .\source.lad .\output.lad --context 20
```

最终报告至少包含：

- 输入和输出绝对路径
- 输入/输出 SHA256 与记录数
- 原生校验 `error_count == 0`
- 修改的记录范围及关键指令预览
- 是否创建 `.bak`，是否处理同名 `.LC`
- 任何被跳过或无法验证的项

## 7. 原生校验配置

```powershell
$env:SYNTEC_LAD_DLL = 'C:\path\to\LadEditor\LadDLL.dll'
$env:SYNTEC_LAD_EDITOR = 'C:\path\to\LadEditor\MLCEdit.exe'
```

也可以给命令传 `--dll`、`--checker` 或 `--editor`。Python 通常是 64 位，
不能直接加载 32 位 DLL；SDK 会用 .NET Framework `csc.exe /platform:x86`
构建并调用 `native/bin/LadSyntaxCheck.exe`。只有
`syntax_ret == 1` 且 `error_count == 0` 才算通过。

若缺少 `LadDLL.dll`、`csc.exe` 或 x86 辅助程序，不要静默跳过并宣称成功。
先修复环境，或明确告知用户只完成了 Python 解析校验。

## 8. 不支持的写入

首版不生成以下新逻辑或新记录：

- 轴控制
- RS232
- Keyscan
- 文件系统
- 外部功能
- Coord/Tool
- FastPLC

遇到这些记录时只做只读检查或逐字节保留，不要尝试重新编码。
