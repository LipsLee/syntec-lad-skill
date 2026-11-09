# Syntec Ladder SDK / Codex Skill

一个无第三方 Python 依赖的 Syntec LadEditor 2.37.0 梯形图 SDK、CLI 和
Codex Skill。它直接读写原生 `.lad` 记录，可检查程序结构、根据审阅过的
JSON 计划生成新梯形图、按哈希锁定的记录范围修补现有文件、批量重映射
PLC 位地址，并调用原厂 32 位 `LadDLL.dll` 做语法校验。

本工具不会点击 LadEditor 界面，不会自动上传 CNC，也不会直接修改控制器上
的 PLC。首版可写范围仅限本地原生 `.lad` 文件。

## 功能

- `inspect`：查看编码、SHA256、程序边界、物理记录、注释和网络候选。
- `build`：从 `syntec-lad-plan/v1` 生成主程序、普通子程序、触点、线圈、
  定时器、计数器、比较、数据传送和跳转/调用。
- `patch`：用 `syntec-lad-change/v1` 对哈希锁定的记录范围做插入、替换、
  删除；过期哈希、跨网络边界和结构行会被拒绝。
- `remap`：同时替换 I/O、C/S/A 位和 R 寄存器位，避免级联误替换。
- `validate`：Python 回读校验，并在可用时调用原厂 LadDLL 语法检查。
- `diff`：按记录和指令单元比较两个 `.lad` 文件。
- `open`：仅启动 `MLCEdit.exe -f <file>` 供人工复核。

## 环境要求

- Windows 10/11（原生校验和 `open` 必需；纯解析/生成可在其他系统运行）。
- Python 3.10 或更高版本，无第三方 Python 依赖。
- 原厂校验：32 位 `LadDLL.dll`、.NET Framework 4.x、可用的 `csc.exe`。
- 人工复核：`MLCEdit.exe`。

如果 LadEditor 不在默认位置，先设置：

```powershell
$env:SYNTEC_LAD_DLL = 'C:\path\to\LadEditor\LadDLL.dll'
$env:SYNTEC_LAD_EDITOR = 'C:\path\to\LadEditor\MLCEdit.exe'
```

## 安装为 Codex Skill

```powershell
$skills = Join-Path $HOME '.codex\skills'
New-Item -ItemType Directory -Force -Path $skills | Out-Null
git clone https://github.com/LipsLee/syntec-lad-skill.git (Join-Path $skills 'syntec-lad')
```

重新打开或刷新 Codex 后，可显式调用：

```text
$syntec-lad
```

也可以从 [GitHub Releases](https://github.com/LipsLee/syntec-lad-skill/releases)
下载 ZIP，解压后把文件夹命名为 `syntec-lad` 并放入 `~/.codex/skills/`。

Skill 自带启动器，不要求安装 Python 包：

```powershell
$LadCli = Join-Path $HOME '.codex\skills\syntec-lad\scripts\run_syntec_lad.py'
python $LadCli --help
```

## 直接使用 CLI

在仓库根目录执行：

```powershell
python -m pip install -e .
syntec-lad --version
```

或完全不安装：

```powershell
python .\scripts\run_syntec_lad.py --help
```

## 快速开始

```powershell
python $LadCli inspect .\file.lad
python $LadCli build .\plan.json .\new.lad
python $LadCli validate .\new.lad
python $LadCli diff .\file.lad .\new.lad
python $LadCli open .\new.lad
```

修补现有文件前，先取得目标记录范围和哈希：

```powershell
python $LadCli inspect .\source.lad --json --output .\source.inspect.json
python $LadCli patch .\source.lad .\change.json .\patched.lad
```

只有用户明确要求覆盖时才使用 `--in-place`。SDK 会创建带时间戳的 `.bak`，
并在同名 `.LC` 侧车文件存在时先备份 `.LC`。

### Plan 示例

完整 Schema 位于 `syntec_lad/schema/lad-plan.schema.json`。

```json
{
  "schema": "syntec-lad-plan/v1",
  "encoding": "gb2312",
  "programs": [
    {
      "name": "MAIN",
      "networks": [
        {
          "comment": "启动、停止与自锁",
          "condition": {
            "all": [
              {"contact": "I000"},
              {"any": [{"contact": "I001", "normally": "closed"}, {"contact": "O000"}]}
            ]
          },
          "actions": [{"op": "OUT", "operand": "O000"}]
        }
      ]
    }
  ]
}
```

以上 I/O 地址只是格式示例，不能直接用于真实设备。

### Change 示例

完整 Schema 位于 `syntec_lad/schema/lad-change.schema.json`。

```json
{
  "schema": "syntec-lad-change/v1",
  "base_sha256": "<inspect 输出的 source.sha256>",
  "operations": [
    {
      "op": "replace",
      "record_start": 19,
      "record_end": 26,
      "expected_sha256": "<对应 network candidate 的 sha256>",
      "networks": [
        {
          "condition": {"contact": "I010"},
          "actions": [{"op": "OUT", "operand": "O010"}]
        }
      ]
    }
  ]
}
```

## 安全边界

- 原生 `.lad` 是唯一可写源格式；`.il`、PDF、BMP 和截图不能作为生成输入。
- 不自行分配 I/O、寄存器、定时器、计数器、预置值或联锁关系。
- 不生成新的轴、RS232、Keyscan、文件系统、外部功能、Coord/Tool 或
  FastPLC 记录。
- 未知或高级记录必须逐字节保留。
- `build`/`patch` 默认同时执行 Python 回读和 LadDLL 原生校验；任一失败
  都不应覆盖目标文件。
- `open` 仅用于人工复核，不把编辑器打开成功当作语法通过。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖记录编解码、并/串联与嵌套网络、定时/计数/比较/数据传送/子程序、
哈希锁定补丁、同步重映射、原子写入、真实 `cnc.lad` 往返，以及可用的
LadDLL 原生校验。真实 `cnc.lad` 路径不存在时会自动跳过该样本测试。

## 目录

```text
SKILL.md                   Codex Skill 入口与强制安全流程
agents/openai.yaml         Skill 展示信息
scripts/run_syntec_lad.py  免安装启动器
syntec_lad/                Python SDK、CLI 和 JSON Schema
native/                    x86 LadDLL 校验辅助程序源码
tools/lad_parser.py        独立的旧版解析/反汇编交叉检查器
examples/                  格式示例与回归夹具
tests/                     unittest 测试
```

版本：`0.1.0`
