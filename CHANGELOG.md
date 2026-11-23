# Changelog

## 0.2.3 - 2026-11-23

This release keeps the `LipsLee/syntec-lad-skill` package identity and version
line at `0.2.3`, while integrating the validated capabilities from
`Aoliaosiki/Syntec-lad-skill-P` 0.3.6p. The 0.3.x sections below are retained
as upstream provenance, not as the current package version.

### Added

- `plan-lint` plan audit before generation, including DNF-equivalent
  conditions, adjacent unconditional networks, mixed action widths, and flow
  actions that cannot share a bus.
- `lint` static rules for duplicate coils, `S` used as a coil, reused I/O,
  out-of-range operands, simplified-Chinese comments, and mergeable networks.
- `discover` for MB/SB/package backup layouts and `crosscheck` for parameter,
  language, alarm, and I/O consistency checks.
- `zhconv` and knowledge-base fallback search for process terms.
- `auto` layout that preserves nested `all`/`any` trees, factors shared
  series conditions, and can place several actions behind one condition bus.
- `inspect --program` / `--network` scoped inspection.
- Segment-aligned `diff v2` with operand-level changes.
- Native DLL compatibility for builds that lack ordinal `#124`, plus bounded
  native-tool discovery with a local cache.
- CodeBuddy installation guidance and the upstream delivery-layout reference.

### Changed

- Version metadata, CLI output, MCP `serverInfo`, and package documentation
  now report `0.2.3`.
- Kept the original hash-pinned patch, remap, Python readback, native LadDLL
  gate, and `references/panming/` knowledge snapshot.
- Excluded the upstream unsigned prebuilt `native/bin/LadSyntaxCheck.exe`;
  the helper is built from `native/LadSyntaxCheck.cs` when needed.

### Upstream provenance

- Source: `Aoliaosiki/Syntec-lad-skill-P`
- Commit: `4e055e41db1e8ad0f4dd386bfe796122fa4500ad`
- Source version: `0.3.6p`

### Tests

- `python -m unittest discover -s tests -v`
- 122 tests passed, 0 skipped with a real `SYNTEC_LAD_SAMPLE` and the
  native 32-bit LadDLL checker available.

## 0.3.6p - 2026-11-23

### Changed

- 原生 DLL 自动发现不再写死个人 D 盘安装目录。固定 C 盘标准路径和
  skill 自带目录优先；找不到时才搜索其它本地固定盘。
- 兜底搜索只查找 `LadDLL.dll` / `MLCEdit.exe`，优先进入
  `SYNTEC`、`LadEditor`、`MacroDev` 等目录，并限制搜索深度和目录数量。
  搜索会跳过 Windows、回收站、`node_modules`、虚拟环境、缓存等目录。
- 成功发现的路径写入
  `%LOCALAPPDATA%\syntec-lad\native-discovery.json`，后续命令直接复用。
- README、CHANGELOG 和测试中的个人 D 盘路径已移除。

### Tests

- 新增自定义盘符布局搜索、固定路径优先、依赖目录跳过和个人路径检查测试。

## 0.3.5p - 2026-11-23

### Fixed

- 原生校验自动发现新增 MacroDev 标准安装路径（32/64 位
  `Program Files`）以及本机 LadEditor 2.37.0 安装目录，无需再手动设置
  `SYNTEC_LAD_DLL` / `SYNTEC_LAD_EDITOR`。

### Verified

- 本机 `LadDLL.dll`（2014-05-30，49152 字节）原生校验通过：
  `examples/demo.lad`、`examples/mvp.lad` 均为 `native: PASS error_count=0`。
- 本机 LadEditor 2.37.0 的 `LadDLL.dll`（2025-08-27，303104 字节）同样
  通过，`MLCEdit.exe` 也可用。
- 新混合动作布局 `TIM+OUT` 与 9 触点双线圈布局均通过原生语法检查。
- 完整测试 118 项通过，11 项跳过（缺少真实样本/编辑器，不再因 DLL 缺失跳过）。

## 0.3.4p - 2026-11-23

### Added

- 同一 network 的多个 `actions` 共用条件时，生成器只排一次条件，并在动作列
  前画一条竖直母线，把线圈/定时器上下并联；不再为每个动作重复整组条件。
- `lint` 新增 `mergeable_condition` 警告：相邻网络条件完全相同时提示合并到
  同一 `actions` 数组。
- 新增 `plan-lint`：在生成 `.lad` 之前直接审计 JSON 计划。条件按 DNF
  规范化后比较，触点顺序不同、`A∧(B∨C)` 与 `(A∧B)∨(A∧C)` 等展开写法
  不同、以及相邻无条件网络都会被识别为可合并。
- 动作母线支持 1 格线圈与 2 格定时器/计数器/流程指令混排：母线位于第 8
  列，线圈前保留常规的第 8 列 LINE，不再为每个动作重复整组条件。只有条件
  本身占满第 8 列时才回退，并由 `plan-lint` 报
  `mergeable_condition_too_wide` / `mixed_action_too_wide`。
- `JMP`/`JSR`/`MSG` 保持独立行，不进入并行动作母线；`plan-lint` / `lint`
  报 `flow_action_not_mergeable`。

### Changed

- `SKILL.md` / `references/workflow.md` 明确：同条件多输出写同一 network；
  只有部分条件相同时不得擅自新增中间继电器；生成前必须先跑 `plan-lint`。
- `lint` 的合并判断改为按实际条件宽度和动作起始列计算；条件放不下共用母线
  时不再误报“可直接合并”，而是报 `mergeable_condition_too_wide`。
- 版本号 0.3.4p。

### Tests

- `tests/test_layout.py` 增加双线圈共条件的两行布局与逐动作导通断言。
- `tests/test_lint.py` 增加重复条件网络告警断言。
- `tests/test_plan_lint.py` 覆盖条件重排、DNF 等价、无条件网络、混合动作
  宽度和渲染输出；`tests/test_cli.py` 覆盖 `plan-lint` 命令。
- `tests/test_layout.py` 增加 1 格线圈 + 2 格定时器共条件、以及 9 触点条件
  驱动双线圈的布局与仿真断言。

## 0.3.3p - 2026-11-23

### Fixed

- `auto` 布局不再先把条件展开成 DNF。编译器直接按 `all`/`any` 树递归排布：
  `all` 的子节点从左到右串联、顶部对齐；`any` 的子节点上下并联，左右各画
  一条跨全部行的母线。母线只在需要的位置补 NOP 锚点，较矮的行不会被错误
  地补上 LINE 而绕过串联元件。
- 修复“嵌套并联 + 外层 OR”无法因式分解时回退成逐项展开的问题。典型网络
  `(A040 ∧ (A012∨A013) ∧ (S069∨R075.00) ∧ ¬A031) ∨ (A043 ∧ ¬A044)`，
  再串联 `A051 ∧ ¬R044.05`，现在写 3 行而不是 5 行；`A040` 只出现一次，
  `A043/¬A044` 支路从下方并入 `A051` 前的母线。

### Changed

- `layout: auto` 的回落顺序改为：树形布局 → 笛卡尔积因式分解 → `expanded`。
- `tests/test_mvp.py` 的“过宽”负例从 9 触点改为 10 触点：9 触点并联现在
  可以在动作列上与母线汇合，属于合法布局。
- 版本号 0.3.3p。

### Tests

- `tests/test_layout.py` 新增嵌套并联网络 3 行断言与 `expanded` 5 行回退断言。
- 用例总数 98 → 100；真实 A43 网络在生成文件上做 1024 组真值表仿真，
  0 差异；LadDLL 原生校验 `error_count=0`。

## 0.3.2p - 2026-11-23

### Added

- 编译器新增 `auto` 布局：当 DNF 恰好是各位置选择的笛卡尔积时，自动因式
  分解为“串联 + 并联组”，公共前缀和后缀只写一次。例如
  `A040 ∧ (A012∨A013) ∧ (S069∨R075.00) ∧ A031 ∧ A051 ∧ R044.05`
  以前会展开成 4 行、每行重复 `A040/A031/A051/R044.05`；现在为 2 行。
- 支持一段串联中出现多个并联组。每个母线边界按 MLCEdit 的原生格式在两侧
  同时写 RD/RU 与 LD/LU 标志；不同高度的并联组、2 槽比较分支、行尾并联
  组都能布局。
- 计划级和网络级 `layout` 字段：`auto`（默认）或 `expanded`（0.3.1 的
  一项一行布局）。不能安全因式分解时自动回落到 `expanded`。
- `tests/test_layout.py`：把编译后的行解析成梯形图图结构，对条件做真值表
  等价检查，并保留原生 LadDLL 校验。

### Changed

- `examples/demo.lad`、`examples/mvp.lad` 更新为因式分解后的布局，逻辑
  不变。
- `test_cli.test_remap_workflow` 的 `changed_operands` / `changed_records`
  期望值随公共条件不再重复而从 6/5 调整为 5/4。
- `syntec_lad/schema/lad-plan.schema.json` 接受 `layout` 字段。

### Tests

- 用例总数 91 → 98；`unittest discover` 全通过（原生 DLL 可用时）。

## 0.3.1p - 2026-11-19

### Added

- `discover`：识别 MB / SB / package 三种备份的固定目录结构，自动定位
  `.lad`、`paramext.pxt`、`ParamExt_*.xml`、`AlarmPLC_*.xml`、IO 表；
  散装单文件直接给路径。结构说明见 `references/delivery-layout.md`。
- `crosscheck`：一次做四项跨文件核对
  1. 参数表 ↔ 梯图（pxt 有节但梯图零引用 / 文案与节不对应）；
  2. 三语 ↔ 三语（生效文案差集，自动排除 XML 注释块）；
  3. 报警位 ↔ 文案（梯图写出的 R44~R49 报警位、R590/R591 提示位）；
  4. IO 表 ↔ 梯图（点位只在一侧存在）。
  支持 `--delivery` 一键取齐所有输入。
- `lint`：静态规则检查——重复线圈、`S` 当线圈写、同一 I/O 点被过多网络
  复用、地址越界（A/C/S 511、TM/CT 255、R 65535）、注释含简体字。
- `zhconv`：简繁转换（域内字符表），用于写注释前把简体转成 cp950 繁体。
- `diff` 的 modified 条目新增 `operand_changes`：操作数级差异
  （如 `OUT R0591.04 -> OUT R0590.04`），不再只有十六进制字节。
- `lookup` 地址无命中时回落到知识库全文检索，并给出工艺词对应的参考文件；
  繁体查询会先归一化为简体再检索。
- MCP 新增 `lint_lad`、`crosscheck_lad` 两个工具。

### Changed

- 报警位缺文案按严重度区分：`R44~R49` 报 error，`R590/R591` 提示位报 warning。
- IO 表解析兼容 `地址<TAB>描述` 文本与 `IoMap_*.xml` 端口映射表；后者解析不出
  点位时只给提示，不刷警告。

### Tests

- 新增 `tests/test_diff.py`、`tests/test_lint.py`、`tests/test_crosscheck.py`、
  `tests/test_zhconv.py`，用例总数 65 → 91。
- 真实交付验证：`crosscheck --delivery <MB 备份>` 与 1028 天樱交付（3191 记录）
  均能跑通，1028 交付结果为 error 0 / warning 5 / info 80。

## 0.3.0p - 2026-11-19

首个公开预览版（Public Preview）。内容 = 0.2.2 的基础上合入全部 0.2.3 修复，
并把版本号、打包与文档对齐到可独立发布的形态。

### Added

- 独立分发包 `downloads/syntec-lad-skill-0.3.0p.zip`，附
  `downloads/syntec-lad-skill-0.3.0p.zip.sha256` 校验文件。
- `README.md` 新增「与原版差异」「来源与授权」两节，说明本仓库是
  `LipsLee/syntec-lad-skill` 的衍生版本。
- `README.md` 增加 CodeBuddy 安装路径（`~/.codebuddy/skills/syntec-lad/`）。
- `.gitignore` 覆盖 `work/`、`*.inspect.json`、`__pycache__/` 等本地产物。

### Changed

- 版本号统一为 `0.3.0p`（CLI `--version`、MCP `serverInfo`、README、ZIP）。
  `pyproject.toml` 保持 PEP 440 合法的 `0.3.0`。
- `README.md` 的 `diff` 说明由「按记录和指令单元比较」改为段对齐描述。

### Fixed

（继承 0.2.3，见下）

## 0.2.3-preview - 2026-11-10

### Fixed

- Native syntax check: ordinal `#124` (`GetSyntaxErrType`) is now optional.
  Some LadDLL builds export only 23 ordinal functions and lack `#124`, which
  made every native validation fail before the syntax gate even ran. The
  helper now reports `error_type=-1` when the export is missing.
  (`native/LadSyntaxCheck.cs`, rebuilt `native/bin/LadSyntaxCheck.exe`.)
- `diff` was rewritten as segment-aligned diff (`syntec-lad-diff/v2`). The
  legacy diff compared records by list index, so one inserted record cascaded
  into thousands of false differences. It now aligns comment blocks, network
  candidates, and structural records by content hash (LCS via
  `difflib.SequenceMatcher`), classifies them as unchanged / modified / added
  / removed / moved, and reports modified networks with instruction previews
  and 4-byte cell deltas instead of raw hex dumps.

### Added

- `inspect --program NAME` / `inspect --network Nxxxx` (and MCP `inspect_lad`
  `program` / `network` arguments) to scope the output to one program or one
  network candidate instead of dumping the whole file.
- `tests/test_patch.py` and `tests/test_native_safety.py` accept a
  `SYNTEC_LAD_SAMPLE` environment variable pointing at a real `cnc.lad`;
  previously the sample path was hardcoded to the author's machine.
- `SKILL.md` documents CodeBuddy (`~/.codebuddy/skills/syntec-lad/`) as a
  first-class host alongside Codex and WorkBuddy.

### Tests

- `python -m unittest discover -s tests` with `SYNTEC_LAD_SAMPLE` pointing at
  a real 119 KB `cnc.lad` (big5, 2991 records) and a 32-bit `LadDLL.dll`:
  65 tests passed, 2 skipped (native fixtures unavailable outside Windows).
- Real-file diff verification: 1026 revision vs 1109 revision now reports
  `unchanged 1708 / modified 5 / added 2` where the legacy diff reported 2893
  cascading position changes.

## 0.2.2 - 2026-11-09

### Fixed

- WorkBuddy Skill backups now live under `~/.workbuddy/skills-backups/`
  instead of inside the scanned `skills/` directory, preventing a backup from
  appearing as a duplicate skill.

### Tests

- 65 tests passed, including the WorkBuddy backup-location regression test.

## 0.2.1 - 2026-11-09

### Fixed

- Documented the WorkBuddy 5.9.3 MCP Servers flow and verified that
  `~/.workbuddy/mcp.json` is read as a stdio server configuration.

## 0.2.0 - 2026-11-09

### Added

- MIT-licensed engineering knowledge snapshot from
  `panming/syntec-plc-ladder` under `references/panming/`.
- 602-address C/S/R index in Markdown and CSV form.
- `syntec-lad lookup <address|keyword>` for the bundled address index.
- Dependency-free MCP stdio adapter for WorkBuddy, Claude Desktop, Cursor,
  and other MCP clients.
- WorkBuddy/Agent Skills installer with backup and self-check behavior.
- Address notation adapter for short upstream forms and SDK canonical forms.
- Knowledge routing, source hierarchy, and upstream attribution notices.
- Expanded `SKILL.md` workflow covering address classes, process patterns,
  field conventions, comments, validation, and critical anti-patterns.

### Preserved

- Native `.lad` parsing, deterministic JSON plans, hash-pinned patches,
  remapping, Python readback, and the native `LadDLL.SyntaxCheck` gate.
- Byte-for-byte preservation of unknown records and the existing safety
  boundaries.

### Tests

- `python -m unittest discover -s tests -v`
- 64 tests passed, including native validation and MCP tests.
