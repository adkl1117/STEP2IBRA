# STEP2IBRA V0.5 Preview / V0.5 预览版

## Downloads / 下载

Download the complete plugin package below. FreeCAD, Python and backend binary dependencies are installed separately. The ZIP contains conversion source, three V0.5 examples, the installer, requirements and bilingual documentation. Use an independent FreeCAD user profile to keep an existing V0.2 installation.

下载下面的完整插件包。FreeCAD、Python 与后端二进制依赖需要另外准备。ZIP 包含转换源码、三个 V0.5 算例、安装器、依赖清单和中英文文档。建议使用独立的 FreeCAD 用户目录，保留已有 V0.2 安装。

- [Plugin package / 完整插件包](https://github.com/adkl1117/STEP2IBRA/releases/download/V0.5preview/STEP2IBRA-V0.5preview-20260927.zip)
- [Installation — English](https://github.com/adkl1117/STEP2IBRA/releases/download/V0.5preview/V0_5_INSTALLATION_en.md)
- [安装指南 — 中文](https://github.com/adkl1117/STEP2IBRA/releases/download/V0.5preview/V0_5_INSTALLATION_zh.md)
- [Release notes and usage — English](https://github.com/adkl1117/STEP2IBRA/releases/download/V0.5preview/V0_5_RELEASE_NOTES_en.md)
- [版本说明与使用说明 — 中文](https://github.com/adkl1117/STEP2IBRA/releases/download/V0.5preview/V0_5_RELEASE_NOTES_zh.md)

## Features / 功能

- Selectable English/Chinese plugin interface with saved preference and immediate updates.
- Coupling-edge isolation, Edge/Patch/Coupling hover identification and table-to-view highlighting.
- Guided left-click selection and middle-click confirmation for boundary/initial-condition targets and existing candidate interfaces. The panel continuously shows the next action.
- Units, material/section thickness, explicit interface decisions, analysis domains, named groups and document association with geometry-change invalidation.
- Preprocessing inspection, reference-normal pressure, time amplitudes, Kratos export/initialization/solution and native VTKHDF displacement/deformation viewing.
- Three editable examples: single-patch cantilever, two-patch continuous shell and perforated trimmed plate.

- 可选择并记忆中英文界面，切换立即生效。
- 一键只显示耦合边，悬浮查看边/面/耦合编号，表格与视图高亮联动。
- 边界/初始条件及已有候选接口支持左键点选、中键确认，面板持续提示下一步操作。
- 单位、材料与截面厚度、显式接口决策、分析域、命名组和带几何变更保护的文档关联。
- 前处理查看、参考法向压力、时间幅值、Kratos 导出/初始化/求解及原生 VTKHDF 位移和变形查看。
- 三个可编辑算例：单片悬臂、双片连续壳、带孔修剪板。

## Verification and limits / 验证与限制

Validated locally on Windows x64 and FreeCAD 1.1.3 with the existing Kratos 10.4.3 backend. The mouse/language/results regression selection passed 169 tests; native FreeCAD checks covered live language switching, preference restoration and the deployed preview. The three example stages have their own Kratos/FreeCAD acceptance evidence. Packaging additionally checks archive integrity, exact source bytes and example STEP references.

This is a preview for surface shells and membranes. Geometric adjacency does not establish mechanical connection intent. Pressure uses the reference configuration, not follower pressure. The native result viewer lacks complete CAD Face/UV mapping; physical penalty-support reactions and global equilibrium are not verified. General beam, solid and contact analysis are outside this release. Review the full usage notes before analysis.

已在 Windows x64、FreeCAD 1.1.3 和现有 Kratos 10.4.3 后端验证。鼠标、语言与结果界面的定向回归共 169 项通过，并进行了原生 FreeCAD 的实时语言切换、偏好恢复和预览部署检查。三个算例各有 Kratos/FreeCAD 阶段验收证据。打包另检查压缩包完整性、源码字节一致及算例 STEP 引用。

本预览版用于曲面壳和膜。几何邻接不代表已确定力学连接；压力按参考构形施加，暂不支持随动压力。原生结果缺少完整 CAD 面/UV 映射；罚约束的物理支承反力和全局平衡尚未验证。一般梁、实体和接触分析不在本版范围内。计算前请阅读完整使用说明。

V0.2's tag and original `freecad-ibra-preprocessor` branch are preserved. This is a prerelease and is not marked as the latest stable version.

保留 V0.2 标签及原 `freecad-ibra-preprocessor` 分支；本版标记为预发布，不替代稳定版本。
