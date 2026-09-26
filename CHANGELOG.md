# Changelog / 更新记录

## V0.5preview — 2026-09-26

Preview release based on the unchanged V0.2 tag. It adds staged IBRA pre-processing, safer project/entity binding, pressure and time-table support, native VTKHDF result display, and three editable schema-3 examples. See the [English](docs/V0.5preview_RELEASE_NOTES.md) and [中文](docs/V0.5preview_RELEASE_NOTES_zh.md) release notes for scope and known limits.

预览版以未改动的 V0.2 标签为基线，增加分阶段 IBRA 前处理、项目与实体绑定保护、压力和时间表、原生 VTKHDF 结果查看，以及三个可编辑的 schema-3 算例。功能范围和限制见[英文发布说明](docs/V0.5preview_RELEASE_NOTES.md)与[中文发布说明](docs/V0.5preview_RELEASE_NOTES_zh.md)。

## V0.2 — 2026-09-16

This tag publishes the local UI and refinement updates to the 0.2.0 distribution.
本版本发布基于 0.2.0 分发版的本地界面与细化功能更新。

### 更新内容

- **Boundary Conditions**：将 Support 与 Loads 合并到 Boundary 导航页，在页内通过两个标签切换。
- **可选 Refinement**：移入 Geometry 页，文字后增加复选框。新项目默认关闭；勾选后启用并展开设置，取消后禁用并隐藏。
- **真实禁用细化**：关闭时不升阶、不插入节点，导出不运行 `RefinementModeler`；保留原有参数供再次启用。开关随项目保存，未包含该字段的旧项目保留原有细化行为。
- **独立材料分配**：Materials 页增加 Patch Assignments 表，材料、壳/膜单元和积分点设置不依赖细化开关。
- **校验**：关闭细化时使用原始曲面阶次判断单元适用性；不满足壳单元最低阶次时明确报错，不自动升阶。
- **界面文字**：顶栏右侧更新为 `KRATOS Multiphysics`。
- **启动修复**：补齐 `InitGui.py`，使 FreeCAD 启动时自动注册工作台。
- 更新中英文使用说明和 GUI 检查脚本；增加细化关闭、保存重开、重新启用及旧项目兼容性回归测试。

### Changes

- Combine Support and Loads under Boundary Conditions with separate inner tabs.
- Move Refinement into Geometry with a checkbox after its label; new projects start with refinement disabled.
- Disabled refinement preserves source degrees and control points and omits `RefinementModeler`. Retain settings for re-enabling, persist the switch, and preserve legacy project behavior.
- Keep material, element and quadrature assignments available in Materials → Patch Assignments.
- Reject insufficient original shell degrees when refinement is disabled instead of silently elevating them.
- Display `KRATOS Multiphysics` in the header and register the workbench automatically through `InitGui.py`.

### Validation / 验证

- Windows x64, FreeCAD 1.1.3, Python 3.11.9, OCP 7.8.1.1 and Kratos applications 10.4.3.
- 27 automated regression tests, including real Kratos initialization with refinement disabled and the original four control points.
- Native FreeCAD GUI checks for workbench registration, checkbox behavior, boundary editors, model validation and refinement-aware export.
- The enabled-refinement cantilever regression retains its analytical displacement target of 0.02 mm.

Save your analysis project and restart FreeCAD after replacing the installed workbench. Keep an existing installation backup outside active `Mod` directories, as described in the installation guide. Dependencies are unchanged.

更新插件前请保存分析项目，更新后重启 FreeCAD。按安装指南将旧插件备份到活动 `Mod` 目录之外。本次更新不改变后端依赖版本。
