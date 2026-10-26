# STEP2IBRA V0.5 Preview：版本说明与使用指南

版本：0.5.0 · 发布日期：2026-09-27 · 工作台：**IBRA Preprocessor for Kratos**

[English version](V0_5_RELEASE_NOTES_en.md) · [安装指南](V0_5_INSTALLATION_zh.md)

## 1. 用途与范围

STEP2IBRA 是面向 Kratos 曲面壳与膜分析的 FreeCAD 工作台，支持等几何边界表示分析（isogeometric B-rep analysis，IBRA）。建模流程保留 STEP 曲面几何及其裁剪信息，并分别定义材料、截面、分析域、边界与初始条件，以及曲面接口的连接决策。

V0.5 增加显式单位换算、接口决策状态、命名组与文档关联、前处理检查、带时间幅值的参考法向压力，以及基于 Kratos 原生输出的位移显示。引导式鼠标点选、耦合边隔离显示和中英文界面用于辅助模型准备。几何检测给出候选接口，其力学作用由使用者根据结构模型确定。

## 2. 启动、项目与界面语言

按[安装指南](V0_5_INSTALLATION_zh.md)完成配置后，在 FreeCAD 中选择 **IBRA Preprocessor for Kratos** 工作台。面板顶部的 **Open Project** 用于打开项目；后端配置及状态位于**输出 → 分析与输出**。

首次使用可打开 `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json`。本文路径相对于解压后的发布包；安装器也会将算例复制到 `Mod/KratosIBRA/examples`。

通过 **Interface language / 界面语言** 选择 **English** 或 **中文**，按钮、导航、字段、工具提示、悬浮信息及点选提示会立即更新，语言选择保留至下次启动。切换保留当前模型、条件草稿、目标与点选阶段，不改变 FreeCAD 全局语言、技术变量、路径、编号或自定义名称。后端日志和数值诊断保留原文。下文保留英文操作名称，便于与英文版及界面对应。

## 3. 几何与实体识别

新建模型时，通过 **Import STEP** 导入 `.stp` 或 `.step` 文件。输入几何采用的转换配置与排除规则应逐项审核。曲面以 B-rep 形状显示，边界采样折线用于点选；导出的分析几何保留 NURBS 曲面和裁剪参数曲线。

**Geometry** 页的 **Hover audit** 提供以下实体识别模式：

| 模式 | 显示内容 |
| --- | --- |
| Edge / Patch / Coupling (all) | 已注册实体的类型与编号 |
| Edge (one-sided boundary) | 普通单侧边界 Edge 编号 |
| Patch (surface) | 曲面 Patch 编号 |
| Coupling (interface edge) | 候选接口 Coupling 编号 |

这些编号对应当前项目的几何映射，不能以 FreeCAD 的 `FaceN` / `EdgeN` 编号替代。

在 **Interfaces** 页选择 **Coupling edges only / 只显示耦合边**，可临时隔离显示耦合边，再次选择后恢复原可见性。选中接口表格行时，对应边会高亮；隐藏接口可临时显示。显示操作不改变分析域或接口决策。

CAD 几何发生变化后，已有绑定会失效。继续导出前，应完成提示的几何核对与重新绑定。

## 4. 支承、载荷与初始场赋值

1. 在 **Boundary / Initial** 中打开相应条件编辑器。
2. 选择 **Start physics picking (left select / middle confirm)**，即**启动物理点选（左键选中键确认）**。
3. 在三维视图左键点选有效几何，并按提示补充所需目标。
4. 在视图中使用中键确认几何，目标随即写入编辑器。
5. 输入条件参数，点击 **Add / Update** 保存记录。

初始场与参考法向压力以曲面为目标。Coupling 边不能作为普通边界条件的边目标。其他文档中的实体、结果及前处理显示对象、失效或存在歧义的映射均会被拒绝。

**New** 清除当前草稿，**Remove** 删除选中记录。按 **Esc** 或使用取消按钮可退出点选。切换页面或条件子页、关闭面板或文档、重新导入几何、退出工作台时，临时交互会结束，并恢复临时修改的可见性。未进行点选时，中键保留正常导航功能。

## 5. 接口决策与耦合

1. 在 **Interfaces** 页选择 **Start interface picking / 启动接口点选流程**。
2. 左键点选第一张候选耦合曲面。
3. 按 **Left-click the other candidate coupling face** 的提示，选择与第一张曲面存在候选接口的另一张曲面。
4. 左键点选对应耦合边；两张曲面之间存在多个接口时，选择所需的一条。
5. 按 **Middle-click the coupling edge again to confirm the target** 的提示，在同一条边上用中键确认。
6. 设置连接类型、罚参数及相关选项，再点击 **Confirm Selected Interface**。**Exclude Selected Interface** 用于排除该候选，**Reset to Candidate** 用于恢复待决定状态。

点选流程配置已存在的候选接口，不会在任意两张曲面之间生成新接口。共享拓扑与空间邻近所对应的连接关系，仍需结合结构模型判断。

位移耦合约束接口两侧的相对平移；壳的弯曲连续性还可能需要转动耦合，此时两侧曲面均须采用壳单元。罚参数应结合模型所需的约束精度、数值条件与收敛性进行评估。

## 6. 材料、单位与分析域

在 **Materials** 中输入弹性模量、泊松比和密度，定义截面厚度，并在 **Patch Assignments** 中赋予材料与截面。每张参与分析的曲面均须具有有效赋值。弹性模量、密度和厚度须为有限正值，泊松比严格位于 −1 与 0.5 之间，材料编号不得重复。

| 单位体系 | 弹性模量 | 密度 | 厚度 | 线载荷 | 面载荷或压力 |
| --- | --- | --- | --- | --- | --- |
| mm-N-s | N/mm² | tonne/mm³ | mm | N/mm | N/mm² |
| m-N-s | Pa | kg/m³ | m | N/m | N/m² |

FreeCAD 的 CAD 长度以毫米存储。单位下拉框选择目标分析体系，**Convert Values to Selected Unit System** 执行已有数值的换算。换算前，应通过 **Add / Update** 提交草稿，或通过 **New** 放弃草稿。涉及转动耦合的共用罚参数可能无法自动换算，应按相应诊断处理。幅值与时间表数值保持不变。

曲面赋值表独立控制分析域，不受显示可见性影响。至少保留一张有效参与分析的曲面；条件目标位于分析域之外时，校验会拒绝该配置。

在 **Groups** 中建立曲面组或边组，供支持该目标类型的条件引用。同一条件不能混用组目标和直接几何目标；组成员缺失会阻止导出。初始场目前采用直接曲面目标。命名组与文档关联依赖明确的几何映射，CAD 变化后应重新审核。

## 7. 离散设置与条件定义

可选细化设置包括 p/q 次数和节点插入。Kirchhoff-Love 壳要求次数至少为 2，内部连续性至少为 C¹；膜要求次数至少为 1，不具有壳的弯曲刚度。升阶不能修复已有的 C0 不连续。显示采样、样条细化和积分点设置分别作用于不同环节。

新项目默认关闭细化。关闭后，原始次数与控制点保持不变，导出不使用 RefinementModeler；再次启用时可恢复已存设置。不含该开关的旧项目保留原行为。原始次数不满足所选公式时，校验会阻止导出。目标次数不得低于原始次数，且不超过 8；每个已有节点区间的节点插入数为 0 至 100。积分点设置为 0 时采用默认值，显式设置范围为每区间 1 至 20 点。输出显示细分范围为 1 至 100。

强约束在支持的控制网位置指定所选位移分量。壳夹持通过约束完整自然参数边上的相邻控制点行实现，不引入独立转动自由度。任意裁剪边和 CAD 顶点不能作为通用夹持目标。罚支承在分析区间内约束受支持边界曲线上的三个位移分量，其罚因子应针对预期约束评估。

载荷包括曲面或边上的向量载荷，以及标量参考法向压力。压力正负与显式翻转共同决定方向。参考法向由参考几何的 `du × dv` 确定，不保证与 CAD 实体外法向一致。非线性分析中的压力仍依据参考构形施加，暂不支持随动压力。

幅值表至少包含两个有限值点，时间严格递增。幅值按分段线性方式插值，载荷在作用区间外为零。表中拐点不会自动转化为求解时间步，应据此选择时间增量。

初始速度和加速度要求动力分析，非零初始位移要求非线性分析。初始场在初始化阶段施加一次，不持续固定自由度。

## 8. 前处理检查与项目保存

在 **Preview** 中选择支承、载荷、参数方向、参考法向及材料截面图层。**Rebuild Preview** 更新图层，**Clear Preview** 清除图层。图形与计数用于检查赋值；积分精度和力学有效性需通过数值检验确认。

**Save Project** 写入 `.ibra-project.json`。在 **Output** 中使用 **Save to Document** 存储文档关联数据后，还须保存 `.FCStd` 文档。重新打开该文档后，使用 **Restore Document** 恢复。移动项目时，应保留其引用的 STEP 与配置文件。几何变化会使原绑定失效，须完成规定的检查与重新绑定。

## 9. 导出、初始化与求解

1. 使用 **Validate Model** 检查输入并消除错误。
2. 使用 **Export Analysis Model →** 导出计算模型。
3. 使用 **Initialize Exported Model** 检查 Kratos 模型初始化。
4. 使用 **Run Exported Analysis** 求解模型。

导出和运行分别使用独立的 `ibra_case*` 与 `ibra_run*` 目录。在 **Output** 中检查后端状态、日志和运行报告。初始化结果对应初始化阶段；求解正常退出或成功显示结果帧，均不能单独证明物理精度。

计算模型分别存储几何、细化、物理赋值、材料与分析设置：

| 文件 | 用途 |
| --- | --- |
| `geometry.cad.json` | NURBS 几何、裁剪边界与耦合拓扑 |
| `refinements.iga.json` | 升阶与节点插入 |
| `physics.iga.json` | 单元、条件及子模型部件赋值 |
| `Materials.json` | 本构、截面与罚参数 |
| `ProjectParameters.json` | 模型构造器、求解器、过程和输出设置 |
| `MainKratos.py` | 分析入口 |
| `ibra_case_process.py` | 初始场及具有作用区间的载荷过程 |
| `project.ibra-project.json` | 可编辑的分析项目快照 |
| `export_report.json` | 导出检查与诊断 |

具备所需后端依赖时，导出模型可脱离 FreeCAD 运行。为保持兼容，官方 Kratos 标识符和 `.iga.json` 扩展名保持不变。仅有 CAD 几何不足以定义分析模型。

在命令行运行时，从解压后的仓库根目录执行以下命令，并将示例目录替换为实际导出目录：

```powershell
& '.\.venv\Scripts\python.exe' '.\ibra_case_example\MainKratos.py' --check
& '.\.venv\Scripts\python.exe' '.\ibra_case_example\MainKratos.py'
```


## 10. 结果显示

1. 选择 **Use Last Run**，或通过 **Browse Run…** 指定运行目录。
2. 点击 **Load Result Metadata**。
3. 选择时间帧、位移 X/Y/Z 或模长，并设置变形放大倍数。
4. 点击 **Draw Result Frame**。倍数为 0 时显示参考构形；**Clear Result Display** 清除结果图层。

查看器读取原生 `results.vtkhdf`。显示细分不改变分析离散。原生输出缺少完整 CAD 面/UV 映射，因此显示网格不能独立验证裁剪积分域。强支承的 `REACTION` 与罚约束原始 RHS 分开标识。罚支承反力的带符号物理解释尚未验证，这些字段不足以确认全局力平衡。

## 11. 随附算例与已记录验证

通过 **Open Project** 打开以下项目，运行前检查已有设置：

| 算例 | 相对解压目录的路径 | 主要检查内容 |
| --- | --- | --- |
| 单曲面悬臂 | `freecad/Mod/KratosIBRA/examples/v0_5_cantilever/cantilever.ibra-project.json` | 材料、厚度、夹持、载荷与位移 |
| 双曲面连续壳 | `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json` | 接口决策与壳耦合 |
| 带孔裁剪板 | `freecad/Mod/KratosIBRA/examples/perforated_plate/perforated_plate.ibra-project.json` | 孔洞、裁剪域与压力积分 |

**Open Cantilever Example** 打开早期算例。本版完整悬臂算例应使用上表中的 V0.5 项目。

[验证记录](Validation.md)给出的单曲面位移模长为 0.02000000000080121 mm，解析值为 0.02 mm；双曲面位移模长为 0.1600030769827438 mm，参考值为 0.16 mm。带孔板的记录积分面积为 32.00000000000065 mm²，压力条件 RHS 合计约为 [0, 0, 0.032] N。这些检验对应各自指定的算例与配置。

## 12. 验证边界

已记录的验证采用 Windows x64、FreeCAD 1.1.3 和 Kratos 10.4.3，覆盖三个指定算例，以及接口、语言和结果流程的定向检查。任意 STEP 裁剪配置仍需独立评估。

本版支持曲面壳与膜。通用梁、实体、接触、多物理场分析、体参数化及一般裁剪点支承不在范围内。工程评估应根据模型检查接口意图、裁剪域积分、收敛性和力平衡。罚支承的物理反力与全局平衡仍未完成验证。

鼠标流程已采用原生 FreeCAD 事件与坐标映射检查。实际鼠标操作、导航样式和显示缩放应在拟使用的预览环境中核对。点选或导出被拒绝时，应按提示修正目标与输入，并保留几何失效保护。
