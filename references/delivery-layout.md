# 新代交付/备份的固定目录结构

机台现场拷贝回来的资料有三种典型形态。`syntec-lad discover <目录>` 会自动识别，
`syntec-lad crosscheck --delivery <目录>` 会按下面的位置自动取齐 `.lad`、参数表、
三语文案、报警文案和 IO 表，不需要逐个文件手敲路径。

## 一、MB 备份（`M7R2337_2026xxxx_MBL_xxxxxxxx`）

备份内容直接躺在根目录，常见顶层目录：
`AlarmMacro Application AuthoData BackupOnly BootPicture DataLog DiskA DiskC
DriverParameter HistoryAlarm IOMap Ladder Macro MakerInfo OCRes OPLog Parameter
ParameterExt Registry RotData SPLCAData SriParameter SysData SystemInfoTxt`

与本工具相关的固定位置：

| 内容 | 路径 |
|---|---|
| 梯形图 | `Ladder/cnc.lad` |
| 报警文案 | `Ladder/AlarmPLC_CHS.xml`、`AlarmPLC_CHT.xml`、`AlarmPLC_COM.xml` |
| 扩充参数表 | `ParameterExt/paramext.pxt` |
| 参数文案 | `ParameterExt/ParamExt_CHS.xml`、`CHT`、`COM` |
| 位参数文案 | `ParameterExt/ParamExt_RBit_CHS.xml`（**另一套，不是扩展参数**） |
| IO 硬件映射 | `IOMap/IoMap_Customer.xml`（端口映射，**不是点位表**） |
| 宏程序 | `Macro/`（G/M 码宏，`.mas` 可能是加密包） |
| 宏报警 | `AlarmMacro/AlarmMacro_*.xml` |

## 二、SB 备份（`M6R5123_2026xxxx_SB_xxxxxxxx`）

镜像整块 C 盘，多一层 `DiskC\`：

| 内容 | 路径 |
|---|---|
| 梯形图 | `DiskC/OpenCNC/Ladder/CNC.LAD`（注意大写；同目录可能还有 `DEVELOP.LAD`） |
| 扩充参数表 | `DiskC/OpenCNC/Res/paramext.pxt` |
| 参数文案 | `DiskC/OCRes/CHS/String/ParamExt_CHS.Xml`（CHT / COM 同理） |
| 报警文案 | `DiskC/OCRes/CHS/String/AlarmPLC_CHS.Xml` |
| 宏报警 | `DiskC/OCRes/CHS/String/AlarmMacro_CHS.Xml` |
| IO 硬件映射 | `DiskC/OpenCNC/Data/IO/IoMap_*.xml` |

## 三、安装包（`package_xxxxxxxx`）

结构与 SB 相同，多一层 `PackageDir\`：

```
package_5390349d/PackageDir/DiskC/OpenCNC/Ladder/cnc.lad
package_5390349d/PackageDir/DiskC/OCRes/CHS/String/ParamExt_CHS.Xml
```

## 四、散装单文件

现场常常只拷出一个 `.lad`（或放在 `IO对比\` 这类临时目录）。此时直接给文件路径：

```powershell
syntec-lad inspect .\cnc.lad
syntec-lad lint .\cnc.lad
syntec-lad crosscheck .\cnc.lad --pxt ..\paramext.pxt `
  --param-xml ..\ParamExt_CHS.xml --alarm-xml .\AlarmPLC_CHS.xml
```

## 五、常用命令

```powershell
# 先看这份备份里有什么
syntec-lad discover "D:\...\M7R2337_20260909_MBL_35D3A8B9"

# 一键四项核对（参数↔梯图、三语、报警位↔文案、IO 表↔梯图）
syntec-lad crosscheck --delivery "D:\...\M7R2337_20260909_MBL_35D3A8B9"

# 静态规则检查
syntec-lad lint "D:\...\Ladder\cnc.lad"
```

## 六、容易踩的坑

- **大小写**：`CNC.LAD`、`ParamExt_CHS.Xml`、`AlarmPLC_CHS.Xml` 在不同版本里大小写不一致，
  工具已按不区分大小写匹配；手工写脚本时必须注意。
- **`IoMap_Customer.xml` 不是点位表**：它记录 RIO 端口与 LogicStart/PhysicStart 的硬件映射，
  没有「I013=安全门」这种语义。点位核对要用现场整理的 `地址<TAB>描述` 表（如 `天樱IO.dat`）。
- **`ParamExt_RBit_*.xml` 是位参数**：与 `ParamExt_*.xml` 是两套东西，别混用。
- **`DiskC/OpenCNC/Res/paramext.pxt` 与 `ParameterExt/paramext.pxt`** 位置不同，但格式一致。
- MB/SB 备份里的 `BackupOnly/`、`OPLog/` 是日志，不是配置，不要拿去解析。
