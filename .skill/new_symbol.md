---
name: new-symbol
description: '根据数据手册创建/补全 KiCAD 符号（.kicad_sym），输入可为 PDF、本地资料目录、Markdown 器件概述或厂商网页链接，缺资料时可联网检索器件信息。Use when: 用户要求新建 KiCAD 符号、补全符号库、录入引脚定义、生成 .kicad_sym、核对或修正已有符号的引脚。'
argument-hint: '<PDF | 资料目录 | Markdown | URL | 器件型号> [目标库]'
---

# 根据数据手册创建 KiCAD 符号

把数据手册（PDF、本地资料目录、Markdown 概述或网页）转换成仓库 `symbols/*.kicad_sym` 中的一个符号：确认输入 → 提取引脚表 → 规划布局 → 生成 S 表达式 → 追加进库 → 用 kicad-cli 校验。

## 适用场景

- 用户给出数据手册 PDF、本地资料目录、Markdown 器件概述、厂商产品页链接或引脚表截图，要求“做个符号 / 加个库 / 补全引脚”。
- 用户只给出型号、没有现成资料，需要先联网检索器件信息再建符号。
- 要求核对、修正已有符号的引脚名称、编号、电气类型。
- 要求把某个器件录入本仓库的符号库。

**不适用**：封装（`footpins/`）、3D 模型（`3d_model/`）、原理图图块（`blocks/`）。多单元（multi-unit）符号、异构封装（同一符号多 footprint）脚本不支持，见“常见问题”。

## 依赖

- Python 3.9+；PDF 解析优先 `pymupdf`，缺失时回退 `pypdf`。
- `kicad-cli`（KiCAD 10，`C:\Program Files\KiCad\10.0\bin\kicad-cli.exe`）用于 SVG 导出校验。
- 不要为这个任务修改仓库依赖或安装包；缺少依赖时先告知用户。

脚本：[`./scripts/new_symbol.py`](./scripts/new_symbol.py)，在仓库根目录执行为 `.skill/scripts/new_symbol.py`。

## 流程

### 0. 输入信息

如果用户未提供详细的依据，则需要向用户索取或者确认器件的详细信息。用户提供的信息可能是以下的一种或几种：

- 包含有器件数据手册的本地目录。
- Markdown格式的器件概述文档。
- PDF格式的数据手册文件。
- 包含有器件描述信息的网页URL。
- 器件数据手册下载页面URL。

用户给出的是资料目录时，先列出其中的候选资料，挑出手册或概述再交给下一步：

```powershell
Get-ChildItem -Path "<目录>" -Recurse -File | Where-Object { $_.Extension -in '.pdf', '.md', '.html' }
```

同一个器件有多份资料时，优先顺序为：原厂数据手册 PDF → Markdown 器件概述 → 网页存档。Markdown 与纯文本概述可以直接用 view 工具读取，无需先做格式转换。

如果用户不能提供以上任何一种信息，则需要联网检索该器件的信息，并依据检索结果创建符号。此时必须：

- 记录实际使用的来源 URL，写入 `datasheet` 字段，并在交付说明中列出。
- 优先厂商官网或原厂数据手册；分销商页面、博客等二手资料只用于交叉验证。
- 检索后引脚定义仍不明确时，按“常见问题”最后一条处理：列为待确认项，不要臆造。

这一步只解决“有没有资料”。引脚、封装、库归属等细节留到“符号信息”一步统一处理，不要反复追问用户。

#### 参考资料清单

从网络下载或在线阅读过的资料（数据手册、应用笔记、引脚表截图、分销商页面等），在获取的当下就登记到清单里，不要等交付时凭记忆补。`extract` 下载的文件默认落在系统临时目录，会话结束或重启后可能消失，所以 URL 与本地路径必须当场记下。

| 来源 URL | 本地文件 | 用途 | 获取时间 |
|-|-|-|-|
| `https://.../xxx_datasheet.pdf` | `%TEMP%\new_symbol\xxx\xxx.pdf` | 引脚定义、电气特性 | 2026-11-16 15:20 |

用户自己提供的本地资料（本地 PDF、目录、Markdown）也一并列出，本地文件标“用户提供”，便于交付时区分哪些是随任务新增的资料。清单在“交付”一步原样回报。

### 1. 符号信息

创建符号时需要指定一些信息，默认值如下：

| 信息 | 默认处理 |
|-|-|
| 型号 | 从文件名、文档标题、URL 或型号本身推断；在交付说明中写出依据 |
| 目标库 | 按“选择目标库”表；拿不准放 `General` |
| 封装 | 从数据手册封装章节推断；找不到就留空并在说明中标注待确认 |
| 参考前缀 | IC 用 `U`，连接器用 `J`，二极管用 `D`，晶体用 `Y` |

只有会改变电路含义的信息（引脚定义不清、封装不确定、库归属有歧义）才集中问用户一次，其余按默认值推进。

有些数据手册涵盖的是一整个系列，而不是单一型号：子型号之间引脚排布或功能不同时，必须各自创建符号；引脚与功能完全一致时，按仓库既有做法合并为一个符号（例如 `HT75xx-SOT-23`），差异型号才分开（例如 `INA180A` / `INA180B`、`MX5050T` / `MX5050L`）。

### 2. 分析输入内容

```powershell
python .skill/scripts/new_symbol.py extract "<PDF 路径或 URL>" --pages 1-6 --render
```

- 文本写到 `*.txt`，页面图写到 `*_pNN.png`（默认系统临时目录，`--out` 可指定）。
- 下载或渲染出的文件立刻登记到“参考资料清单”（见“输入信息”一节），交付时需要原样回报。
- `extract` 只接受单个文件或 URL，不接受目录；Markdown 概述也可直接用 view 工具读。
- **必须看图核对**：引脚表、内部连接图常是矢量图或扫描图，文本层里没有。用 view 工具打开对应 PNG。
- URL 是产品页时，脚本会打印候选 datasheet 链接，优先选官方 PDF。
- 需要时用 `--pages` 缩小范围、`--dpi 300` 提高清晰度（引脚密集的 QFN/BGA）。
- 记录：型号、封装、每个引脚的编号/名称/功能/电气属性、电源范围、NC 与 EP、低有效标记、描述、datasheet 链接。

### 3. 选择目标库

| 器件类型 | 库文件 |
|-|-|
| 接口 IC（USB/串口/以太网/电平转换） | `symbols/Interface_IC.kicad_sym` |
| 电源 IC、LDO、DC-DC、充电管理 | `symbols/Power_IC.kicad_sym` |
| MCU/SoC/FPGA/无线模块芯片 | `symbols/MCU_SOC_FPGA.kicad_sym` |
| 传感器 | `symbols/Sensor.kicad_sym` |
| 电机驱动 | `symbols/Motor_Driver.kicad_sym` |
| 射频器件 | `symbols/RF.kicad_sym` |
| 实时时钟 | `symbols/RTC.kicad_sym` |
| 屏幕 | `symbols/Screen.kicad_sym` |
| 连接器、端子 | `symbols/Connector.kicad_sym` |
| 按键、开关、编码器 | `symbols/Button_Switch.kicad_sym` |
| 成品模块 | `symbols/Modules.kicad_sym` |
| 整板外形 | `symbols/Board.kicad_sym` |
| 无法归类 | `symbols/General.kicad_sym` |

```powershell
python .skill/scripts/new_symbol.py check symbols/Sensor.kicad_sym --symbol <型号>
```

- 符号不存在时该命令会打印 `error: symbol not found` 并以退出码 2 结束，这是预期的“库里还没有它”，不是库文件损坏。
- 已存在同名符号时：默认拒绝追加。只有用户明确要求修正时才用 `--replace`，并在说明中列出改动。
- 库文件版本必须 ≥ `20241209`。低版本库先用 `kicad-cli sym upgrade` 单独升级并单独提交，不要混进新符号的 diff。

### 4. 编写 spec（JSON）

写入临时文件（Windows 如 `%TEMP%\new_symbol\<型号>.json`，Linux 如 `/tmp/new_symbol/<型号>.json`；不要提交进仓库）：

```json
{
  "name": "CH340K",
  "reference": "U",
  "value": "CH340K",
  "footprint": "Package_SO:SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm",
  "datasheet": "https://www.wch.cn/products/CH340.html",
  "description": "USB转异步串口芯片，内置时钟和防倒灌二极管，ESSOP-10封装",
  "keywords": ["USB", "串口", "UART", "转换"],
  "fp_filters": "SSOP*3.9x4.9mm*P1mm*",
  "pins": [
    {"number": "1", "name": "UD+", "type": "bidirectional"},
    {"number": "2", "name": "UD-", "type": "bidirectional"},
    {"number": "3", "name": "GND", "type": "power_in"},
    {"number": "4", "name": "~{DTR}", "type": "output", "side": "right"},
    {"number": "5", "name": "~{CTS}", "type": "input"},
    {"number": "6", "name": "~{RTS}", "type": "output", "side": "right"},
    {"number": "7", "name": "VCC", "type": "power_in"},
    {"number": "8", "name": "TXD", "type": "output", "side": "right"},
    {"number": "9", "name": "RXD", "type": "input"},
    {"number": "10", "name": "V3", "type": "passive", "side": "right"},
    {"number": "11", "name": "GND", "type": "power_in", "side": "bottom"}
  ]
}
```

字段：

| 字段 | 说明 |
|-|-|
| `name` / `value` | 符号名与显示值；`value` 省略时等于 `name` |
| `reference` | 参考编号前缀，默认 `U` |
| `footprint` | KiCAD 封装库 ID，`库:封装` 格式；不确定留空 |
| `datasheet` | 手册或产品页 URL |
| `description` / `keywords` / `fp_filters` | 写入隐藏属性；`keywords` 可为数组或空格分隔字符串 |
| `pin_length` | 可选，默认 `2.54` |
| `body` | 可选，`{"width": 20.32, "height": 30.48}` 强制外形尺寸（只放大不缩小） |
| `pins[].number` / `name` | 引脚编号与名称，必填编号 |
| `pins[].type` | 电气类型，见下表；省略按 `passive` |
| `pins[].side` | `left`/`right`/`top`/`bottom`；省略时按名称和类型推断 |
| `pins[].hide` | 是否隐藏；`no_connect` 默认隐藏 |
| `allow_duplicate_numbers` | 可选，`true` 允许重复引脚编号（内部并联）；默认拒绝 |

电气类型（KiCAD 关键字）：

| 场景 | `type` |
|-|-|
| 电源输入（VDD/VCC/VIN） | `power_in` |
| 电源输出（LDO 输出、基准） | `power_out` |
| 数字/模拟输入 | `input` |
| 推挽输出 | `output` |
| 双向 IO、数据总线、USB | `bidirectional` |
| 高阻/三态 | `tri_state` |
| 无源（电阻、电容、晶振、EP） | `passive` |
| 开集/开漏 | `open_collector` |
| 开射 | `open_emitter` |
| 未定义 | `unspecified` |
| 手册标注 NC | `no_connect` |
| 悬空（可外接） | `free` |

引脚名称规则：

- 保持手册写法与大小写；低有效用 KiCAD 上划线语法 `~{RST}`。
- 复用功能用 `NAME/ALT`（例如 `~{DTR}/TNOW`），与仓库现有符号一致。
- 无名引脚写 `~`（脚本对空名称自动处理）。
- EP/散热焊盘按手册编号（可能是 `EP` 或数字），不要与 GND 合并编号；需要并联时允许堆叠坐标。

### 5. 生成并追加

```powershell
python .skill/scripts/new_symbol.py add symbols/Interface_IC.kicad_sym "%TEMP%\new_symbol\CH340K.json"
```

- 先加 `--dry-run` 看生成的 S 表达式，确认无误再去掉。
- 脚本按库版本自动选择 KiCAD 10 / 9 语法，保留原文件的缩进、换行符与 BOM。
- 默认写 `<库>.bak` 备份（`.bak` 已被 `.gitignore` 忽略）。
- 脚本写入前会自检：平衡括号、引脚编号、坐标栅格；失败则不改动库文件。
- **禁止手工编辑几十万行的库文件**，也不要顺手重排格式或改换行符。

### 6. 校验（必须执行）

```powershell
python .skill/scripts/new_symbol.py check symbols/Interface_IC.kicad_sym --symbol CH340K
python .skill/scripts/new_symbol.py render symbols/Interface_IC.kicad_sym --symbol CH340K --out "%TEMP%\new_symbol\svg"
```

- `check` 报 `error` 必须修完；`warning` 逐条判断（堆叠引脚可能是故意的）。
- `render` 成功说明 KiCAD 能解析该库并完成绘制。
- 需要核对隐藏的 NC 引脚时给 `render` 加 `--include-hidden-pins`。
- 用 view 工具看图（`kicad-cli` 只输出 SVG，可用浏览器/VS Code 预览打开），或检查 SVG 里的 `<text>` 标签是否包含全部可见引脚名。
- 人工核对清单：
  - [ ] 引脚总数、编号与手册一致
  - [ ] 引脚名称、低有效标记、复用功能写法与手册一致
  - [ ] 电源在上、地在下的方向合理，输入在左、输出在右
  - [ ] NC 引脚隐藏且类型为 `no_connect`；EP 编号与类型正确
  - [ ] 引脚全部落在 1.27 mm 栅格上，无意外重叠
  - [ ] `Value`/`Footprint`/`Datasheet`/`Description` 已填写
  - [ ] 若改动了已有符号，整库 `check` 与 `render` 仍能通过（允许存在与本任务无关的历史告警，但不得新增 error）

### 7. 交付

**先说成果**：器件型号、目标库、引脚数、封装、datasheet 来源、校验命令结果、备份路径、仍待确认的信息（如暂缺封装）。

**再回报参考资料清单**：把制作期间参考或下载过的资料逐条列出，网络资料与用户提供的本地资料分开标注。

- 来源 URL（本地资料写文件路径并注明“用户提供”）；
- 本地保存位置（如 `extract` 的输出目录）；
- 用途（引脚定义 / 封装尺寸 / 典型应用电路 / 仅作交叉验证）；
- 获取时间。

**然后让用户决定这些资料的去留**：只要本次从网络下载或引用了外部资料，就用提问工具一次性给出三个选项，不要自行处置；若全程只用用户自己提供的本地资料，说明一句“本次未下载外部资料”即可，无需发起选择。

| 选项 | 含义 |
|-|-|
| 保留 | 就地留在临时目录，用户稍后自行取用 |
| 转储 | 复制到用户指定的目录长期保存；路径与命名由用户给定 |
| 清理 | 删除本次产生的资料副本 |

用户未答复前，**不要删除任何从网络获取的文件**；只有用户选择“清理”，或明确表示不需要这些资料时，才删除对应文件。转储到仓库内的资料同样要等用户确认后才考虑纳入版本控制。

**最后清理自产中间文件**：`extract` 生成的文本、渲染的 PNG/SVG、spec JSON、`<库>.bak` 备份（未用于回滚时）。这些在资料去留确定后再清理，并在说明中提一句删掉了什么。

不要自动 `git commit` / `git add`，除非用户明确要求。

## 常见问题

- **文本层为空 / 只有页眉**：扫描件。用 `--render` 生成 PNG 后直接读图；必要时 `--dpi 300`。
- **引脚表跨页或分成多个表**：`--pages 3-5` 逐页提取，按“引脚编号+名称”合并。
- **同一功能多组引脚（多路 UART/GPIO）**：仍按编号逐个录入，名称沿用手册；不要合并编号。
- **多单元器件（如双运放、四与非门）**：脚本只生成单 unit。先与用户确认：拆成多个独立符号（推荐，命名加 `_A`/`_B`）或只做单元 1。
- **隐藏电源引脚**：不要隐藏电源引脚。放到顶部并显示，保证原理图可读。
- **符号名冲突**：加厂商或封装后缀区分，绝不用 `--replace` 静默覆盖他人的符号。
- **库版本过旧**：先单独升级该库再添加符号，避免把整库格式变更混进本次提交。
- **引脚数很多（> 64）**：用 `body` 加大外形，或按功能分侧；坐标仍须落在 1.27 mm 栅格。
- **异构封装（一个符号对应多个 footprint）**：脚本只写一个 `Footprint`。填入最常用的封装，其余在交付说明中注明；确需多封装时由用户手工在 KiCAD 中补充。
- **手册信息矛盾**：以引脚表为准，其次以典型应用电路为准；仍不确定时列为待确认项，不要臆造。

## 脚本命令速查

| 命令 | 用途 |
|-|-|
| `extract <source> [--pages 1-6] [--render] [--dpi 200] [--out DIR]` | 读取 PDF/网页/纯文本/图片（单个文件或 URL，不接受目录），导出文本与页面图 |
| `add <lib> <spec.json> [--dry-run] [--replace] [--no-backup]` | 按 spec 生成符号并追加/替换 |
| `check <lib> [--symbol NAME] [--all]` | 括号、属性、引脚编号/坐标/栅格检查 |
| `render <lib> [--symbol NAME] [--out DIR] [--include-hidden-pins]` | 调用 kicad-cli 导出 SVG |
