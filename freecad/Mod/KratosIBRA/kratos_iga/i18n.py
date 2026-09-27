"""V0.5 selectable English / Chinese interface text.

Pure-Python and FreeCAD/Qt-free so the backend venv can test translation,
formatting, fallback and preference handling without a GUI.

Design rules (contract ``docs/V0_5_LANGUAGE_INTERFACE_zh.md``):

* The catalog key is a **stable message id**, never a user value.  Translating a
  string can therefore never rewrite a user name, an ID, a file path, a unit value
  or a backend raw log line: those travel as ``{placeholder}`` parameters.
* Nothing here stores analysis data, project enums or the FreeCAD global language.
  The only persisted item is :data:`PREFERENCE_GROUP` / :data:`PREFERENCE_KEY`, the
  plugin's own FreeCAD parameter preference.
* The pure interaction core keeps its historical Chinese default.  This module
  treats the Chinese catalog entry as the **source text** of every message, so
  ``translate(key, "zh")`` is byte-identical to the pre-i18n literal.
"""
from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Languages
# --------------------------------------------------------------------------- #
DEFAULT_SOURCE = "zh"
LANGUAGES = ("en", "zh")
LANGUAGE_LABELS = {"en": "English", "zh": "中文"}
DEFAULT_LANGUAGE = "en"          # any FreeCAD language that is not Chinese

_LANGUAGE_ALIASES = {
    "en": "en", "eng": "en", "english": "en", "en-us": "en", "en-gb": "en",
    "zh": "zh", "zho": "zh", "chi": "zh", "chinese": "zh", "zh-cn": "zh",
    "zh-hans": "zh", "zh-sg": "zh", "zh-tw": "zh", "zh-hant": "zh", "zh-hk": "zh",
    "cn": "zh", "chs": "zh",
}

#: FreeCAD preference path (own plugin parameter group, never the global language).
PREFERENCE_GROUP = "Mod/KratosIBRA"
PREFERENCE_KEY = "language"

_LANG_TOKEN = "{lang}"
_CURRENT = DEFAULT_LANGUAGE
_WARNED = set()


def normalize_language(value, default=DEFAULT_LANGUAGE):
    """Return a catalog language for a stored/settings value, or ``default``."""
    if not isinstance(value, str):
        return default
    token = value.strip().lower().replace("_", "-")
    if not token:
        return default
    if token in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[token]
    head = token.split("-", 1)[0]
    return _LANGUAGE_ALIASES.get(head, default)


def language_label(language):
    """The native name of a language for the selector."""
    return LANGUAGE_LABELS.get(normalize_language(language), LANGUAGE_LABELS[DEFAULT_LANGUAGE])


def default_language_from_freecad(freecad_language=None):
    """FreeCAD's interface language decides the first run; anything else is English."""
    if freecad_language is None:
        freecad_language = _freecad_language()
    return "zh" if normalize_language(freecad_language, default="") == "zh" else DEFAULT_LANGUAGE


def _freecad_language():
    """Best-effort read of FreeCAD's global language **for the first run only**."""
    value = os.environ.get("IBRA_FREECAD_LANGUAGE")
    if value:
        return value
    try:  # FreeCAD is optional in the backend venv
        import FreeCAD as _App  # noqa: N813
    except Exception:  # noqa: BLE001 - no FreeCAD means no hint
        return None
    try:
        return str(_App.ParamGet("User parameter:BaseApp/Preferences/General").GetString("Language"))
    except Exception:  # noqa: BLE001 - an unreadable preference is simply no hint
        return None


# --------------------------------------------------------------------------- #
# Catalog.  key -> {"zh": source text, "en": translation}
# ``{param}`` placeholders are filled from the call's keyword parameters.
# Only interface text belongs here: never a user-entered name/ID/value/path and
# never a backend raw log line.
# --------------------------------------------------------------------------- #
GUIDANCE_IDLE_SOURCE = ("鼠标点选：在边界／初始页点击“启动物理点选”，左键选择几何，"
                        "中键确认，Esc 取消；悬浮可查看已注册实体编号。")
MESSAGES = {
    # -- brand and top bar ------------------------------------------------
    "app.title": {"zh": "IBRA Preprocessor for Kratos", "en": "IBRA Preprocessor for Kratos"},
    "brand.heading": {"zh": "IBRA Preprocessor", "en": "IBRA Preprocessor"},
    "brand.caption": {"zh": "面向 Kratos 的 B-rep 结构分析",
                      "en": "B-rep-based structural analysis for Kratos"},
    "brand.badge": {"zh": "KRATOS Multiphysics", "en": "KRATOS Multiphysics"},
    "top.import_step": {"zh": "+ 导入 STEP", "en": "+ Import STEP"},
    "top.open_project": {"zh": "打开项目", "en": "Open Project"},
    "top.save_project": {"zh": "保存项目", "en": "Save Project"},
    "top.language": {"zh": "界面语言", "en": "Interface language"},
    "top.language_tooltip": {
        "zh": "选择插件界面语言。切换后立即更新；草稿、选择、活动点选阶段和项目保持不变。",
        "en": "Select the plugin interface language. Switching updates immediately and keeps "
              "drafts, selections, the active picking stage and the project unchanged."},
    "summary.empty": {
        "zh": "导入 STEP 几何，在裁剪曲面上定义壳或膜模型。",
        "en": "Import STEP geometry to define shell or membrane models on trimmed surfaces."},
    "status.ready": {"zh": "就绪 | 导入几何以定义分析模型。",
                     "en": "Ready | Import geometry to define the analysis model."},
    "log.placeholder": {"zh": "几何转换、模型验证与求解器日志",
                        "en": "Geometry conversion, model validation and solver log"},
    "log.show": {"zh": "显示日志", "en": "Show Log"},
    "log.hide": {"zh": "隐藏日志", "en": "Hide Log"},
    "action.validate": {"zh": "验证模型", "en": "Validate Model"},
    "action.export": {"zh": "导出分析模型  →", "en": "Export Analysis Model  →"},
    "action.stop": {"zh": "停止", "en": "Stop"},
    "action.new": {"zh": "新建", "en": "New"},
    "action.remove": {"zh": "移除", "en": "Remove"},
    "action.add_update": {"zh": "添加 / 更新", "en": "Add / Update"},
    "action.add_row": {"zh": "添加行", "en": "Add Row"},
    "action.remove_row": {"zh": "删除行", "en": "Remove Row"},
    "action.cancel": {"zh": "取消", "en": "Cancel"},
    "action.confirm": {"zh": "确认", "en": "Confirm"},

    # -- navigation and page headings -------------------------------------
    "nav.geometry": {"zh": "几何", "en": "Geometry"},
    "nav.materials": {"zh": "材料", "en": "Materials"},
    "nav.groups": {"zh": "组", "en": "Groups"},
    "nav.boundary": {"zh": "边界", "en": "Boundary"},
    "nav.initial": {"zh": "初始", "en": "Initial"},
    "nav.interfaces": {"zh": "接口", "en": "Interfaces"},
    "nav.preview": {"zh": "预览", "en": "Preview"},
    "nav.analysis": {"zh": "分析", "en": "Analysis"},
    "page.geometry.eyebrow": {"zh": "几何", "en": "GEOMETRY"},
    "page.geometry.title": {"zh": "几何与拓扑", "en": "Geometry and Topology"},
    "page.materials.eyebrow": {"zh": "材料", "en": "MATERIALS"},
    "page.materials.title": {"zh": "本构属性", "en": "Constitutive Properties"},
    "page.groups.eyebrow": {"zh": "组", "en": "GROUPS"},
    "page.groups.title": {"zh": "命名组与重绑定", "en": "Named Groups and Rebinding"},
    "page.boundary.eyebrow": {"zh": "边界", "en": "BOUNDARY"},
    "page.boundary.title": {"zh": "边界条件", "en": "Boundary Conditions"},
    "page.initial.eyebrow": {"zh": "初始状态", "en": "INITIAL STATE"},
    "page.initial.title": {"zh": "初始条件", "en": "Initial Conditions"},
    "page.interfaces.eyebrow": {"zh": "接口", "en": "INTERFACES"},
    "page.interfaces.title": {"zh": "曲面片接口", "en": "Patch Interfaces"},
    "page.preview.eyebrow": {"zh": "预览", "en": "PREVIEW"},
    "page.preview.title": {"zh": "可检查的预处理", "en": "Inspectable Preprocessing"},
    "page.analysis.eyebrow": {"zh": "分析", "en": "ANALYSIS"},
    "page.analysis.title": {"zh": "分析与输出", "en": "Analysis and Output"},
    "page.support_tab": {"zh": "支承", "en": "Support"},
    "page.loads_tab": {"zh": "载荷", "en": "Loads"},

    # -- geometry page ----------------------------------------------------
    "geometry.table.type": {"zh": "Type", "en": "Type"},
    "geometry.table.brep_id": {"zh": "BRep ID", "en": "BRep ID"},
    "geometry.table.patch": {"zh": "Patch IDs / Degrees", "en": "Patch IDs / Degrees"},
    "geometry.open_example": {"zh": "Open Cantilever Example", "en": "Open Cantilever Example"},
    "geometry.profile_placeholder": {
        "zh": "Optional model-specific conversion profile (JSON)",
        "en": "Optional model-specific conversion profile (JSON)"},
    "geometry.select_profile": {"zh": "Select Conversion Profile", "en": "Select Conversion Profile"},
    "geometry.refinement": {"zh": "Refinement", "en": "Refinement"},
    "geometry.refinement_enable": {"zh": "启用细化", "en": "Enable Refinement"},
    "geometry.refinement_tooltip": {
        "zh": "启用升阶与节点插入。不勾选时使用未细化的原始几何。",
        "en": "Enable degree elevation and knot insertion. Unchecked uses the original geometry "
              "without refinement."},
    "geometry.hover_audit": {"zh": "Hover audit", "en": "Hover audit"},
    "geometry.hover_audit_enable": {"zh": "Enable hover audit", "en": "Enable hover audit"},
    "geometry.hover_mode_all": {"zh": "Edge / Patch / Coupling (all)", "en": "Edge / Patch / Coupling (all)"},
    "geometry.hover_mode_edge": {"zh": "Edge (one-sided boundary)", "en": "Edge (one-sided boundary)"},
    "geometry.hover_mode_patch": {"zh": "Patch (surface)", "en": "Patch (surface)"},
    "geometry.hover_mode_coupling": {"zh": "Coupling (interface edge)", "en": "Coupling (interface edge)"},
    "geometry.refinement_hint": {
        "zh": "p/q 为目标多项式次数。h-u/h-v 为每个现有跨度的节点插入数。GP = 0 使用默认积分规则。",
        "en": "Degrees p/q are target polynomial degrees. h-u/h-v specify knot insertions per "
              "existing span. GP = 0 retains the default quadrature rule."},
    "geometry.assignment_hint": {
        "zh": "关闭 Refinement 时这些指定仍然生效。GP = 0 使用默认积分规则。",
        "en": "These assignments remain active when Refinement is disabled. GP = 0 uses default "
              "quadrature."},

    # -- materials page ---------------------------------------------------
    "materials.sections": {"zh": "截面", "en": "Sections"},
    "materials.add": {"zh": "添加材料", "en": "Add Material"},
    "materials.add_section": {"zh": "添加截面", "en": "Add Section"},
    "materials.remove_rows": {"zh": "移除选中行", "en": "Remove Selected Rows"},
    "materials.convert": {"zh": "将数值转换为所选单位体系",
                          "en": "Convert Values to Selected Unit System"},
    "materials.patch_assignments": {"zh": "曲面片指定", "en": "Patch Assignments"},
    "materials.table.id": {"zh": "ID", "en": "ID"},
    "materials.table.name": {"zh": "名称", "en": "Name"},
    "materials.table.thickness": {"zh": "厚度", "en": "Thickness"},
    "materials.table.e": {"zh": "E", "en": "E"},
    "materials.table.nu": {"zh": "ν", "en": "ν"},
    "materials.table.rho": {"zh": "ρ", "en": "ρ"},
    "element.shell": {"zh": "Kirchhoff–Love 壳", "en": "Kirchhoff–Love Shell"},
    "element.membrane": {"zh": "膜", "en": "Membrane"},
    "materials.patch.face": {"zh": "面", "en": "Face"},
    "materials.patch.material": {"zh": "材料 ID", "en": "Material ID"},
    "materials.patch.section": {"zh": "截面 ID", "en": "Section ID"},
    "materials.patch.element": {"zh": "单元", "en": "Element"},
    "materials.patch.gp": {"zh": "高斯积分点", "en": "GP"},
    "materials.patch.active": {"zh": "活动", "en": "Active"},
    "materials.patch.active_tooltip": {
        "zh": "非活动面片不参与物理、材料、细化与 VTK 输出；CAD 几何保留为参考。",
        "en": "Inactive patches are excluded from physics, materials, refinement and VTK output; "
              "the CAD geometry stays as reference."},
    "materials.patch.degree_p": {"zh": "p", "en": "p"},
    "materials.patch.degree_q": {"zh": "q", "en": "q"},
    "materials.patch.hu": {"zh": "h-u", "en": "h-u"},
    "materials.patch.hv": {"zh": "h-v", "en": "h-v"},
    "materials.bulk.material": {"zh": "材料 ID", "en": "Material ID"},
    "materials.bulk.element": {"zh": "单元公式", "en": "Element Formulation"},
    "materials.bulk.min_degree": {"zh": "最小次数", "en": "Minimum Degree"},
    "materials.bulk.knots": {"zh": "每跨节点数", "en": "Knots per Span"},
    "materials.bulk.select_all": {"zh": "全选", "en": "Select All"},
    "materials.bulk.apply": {"zh": "应用到选中曲面片", "en": "Apply to Selected Patches"},
    "materials.current_units": {"zh": "当前项目单位：{units}", "en": "Current project units: {units}"},
    "materials.current_units_pending": {"zh": "当前项目单位：{units}（待应用：{target}）",
                                        "en": "Current project units: {units} (pending: {target})"},

    # -- groups page ------------------------------------------------------
    "groups.table.id": {"zh": "ID", "en": "ID"},
    "groups.table.name": {"zh": "名称", "en": "Name"},
    "groups.table.kind": {"zh": "类型", "en": "Kind"},
    "groups.table.members": {"zh": "成员", "en": "Members"},
    "groups.table.status": {"zh": "状态", "en": "Status"},
    "groups.create": {"zh": "从选择创建组", "en": "Create Group from Selection"},
    "groups.rename": {"zh": "重命名组", "en": "Rename Group"},
    "groups.remove": {"zh": "移除组", "en": "Remove Group"},
    "groups.pending_rebind": {"zh": "待重绑定", "en": "Pending rebind"},
    "groups.rebind_table.entry": {"zh": "条目", "en": "Entry"},
    "groups.rebind_table.kind": {"zh": "类型", "en": "Kind"},
    "groups.rebind_table.old": {"zh": "旧目标", "en": "Old target"},
    "groups.rebind_table.reason": {"zh": "原因", "en": "Reason"},
    "groups.rebind_table.action": {"zh": "操作", "en": "Action"},
    "groups.rebind_selected": {"zh": "重绑定到当前选择", "en": "Rebind to Current Selection"},
    "groups.drop_binding": {"zh": "放弃绑定", "en": "Drop Binding"},
    "groups.rebind_or_drop": {"zh": "重绑定或放弃", "en": "rebind or drop"},
    "groups.snapshot_heading": {"zh": "受控快照与显式重映射",
                                "en": "Controlled snapshot and explicit remap"},
    "groups.snapshot_table.old": {"zh": "旧目标", "en": "Old target"},
    "groups.snapshot_table.kind": {"zh": "类型", "en": "Kind"},
    "groups.snapshot_table.referenced": {"zh": "被引用者", "en": "Referenced by"},
    "groups.snapshot_table.new": {"zh": "新目标", "en": "New target"},
    "groups.snapshot_table.status": {"zh": "状态", "en": "Status"},
    "groups.snapshot.capture": {"zh": "捕获受控快照", "en": "Capture Controlled Snapshot"},
    "groups.snapshot.convert": {"zh": "转换快照（预览）", "en": "Convert Snapshot (preview)"},
    "groups.snapshot.apply": {"zh": "应用显式映射", "en": "Apply Explicit Mapping"},
    "groups.snapshot.cancel": {"zh": "取消快照", "en": "Cancel Snapshot"},
    "groups.snapshot.recover": {"zh": "为快照恢复文档", "en": "Recover for Snapshot"},
    "groups.snapshot.none": {"zh": "没有进行中的快照。", "en": "No snapshot in progress."},
    "groups.snapshot.unresolved": {"zh": "（未解析）", "en": "(unresolved)"},
    "groups.snapshot.drop": {"zh": "（放弃）", "en": "(drop)"},
    "groups.snapshot.face_label": {"zh": "面 {id}", "en": "face {id}"},
    "groups.snapshot.edge_label": {"zh": "边 {id}", "en": "edge {id}"},
    "groups.snapshot.unresolved_status": {"zh": "未解析", "en": "unresolved"},
    "groups.snapshot.confirm_title": {"zh": "确认快照重映射", "en": "Confirm snapshot remap"},
    "groups.snapshot.confirm_text": {
        "zh": "请在提交前审阅该映射带来的具名后果。",
        "en": "Review the named consequences of this mapping before committing."},
    "groups.status_ok": {"zh": "正常", "en": "ok"},
    "groups.status_missing": {"zh": "缺失 {members}", "en": "missing {members}"},
    "groups.direct_selection": {"zh": "（直接选择）", "en": "(direct selection)"},

    # -- condition pages (Support / Loads / Initial) -----------------------
    "conditions.target_placeholder": {"zh": "选择几何实体，然后指定该选择。",
                                      "en": "Select geometric entities, then assign the selection."},
    "conditions.assign": {"zh": "指定所选实体", "en": "Assign Selected Entities"},
    "conditions.start_pick": {"zh": "启动物理点选（左键选中键确认）",
                              "en": "Start physics picking (left select / middle confirm)"},
    "conditions.cancel_pick": {"zh": "取消点选（Esc）", "en": "Cancel picking (Esc)"},
    "conditions.pick_hint": {
        "zh": "鼠标点选：左键点选已注册的 Patch / Edge，中键确认后回到本表单；"
              "初始场与参考法向压力仅接受面。Esc 取消并恢复视图。",
        "en": "Mouse picking: left-click a registered Patch / Edge and middle-click to confirm; "
              "you return to this form. Initial fields and reference normal pressure accept "
              "faces only. Esc cancels and restores the view."},
    "conditions.name": {"zh": "名称", "en": "Name"},
    "conditions.named_group": {"zh": "命名组", "en": "Named Group"},
    "conditions.interval": {"zh": "活动时间区间", "en": "Active Time Interval"},
    "conditions.list_hint": {"zh": "双击条件进行编辑。New 会退出当前编辑模式。",
                             "en": "Double-click a condition to edit it. New exits the current "
                                   "edit mode."},
    "conditions.support_method": {"zh": "执行方式", "en": "Enforcement"},
    "conditions.support_method_strong": {"zh": "强执行 | 控制点",
                                         "en": "Strong Enforcement | Control Points"},
    "conditions.support_method_penalty": {"zh": "罚函数执行 | 边界 XYZ",
                                          "en": "Penalty Enforcement | Boundary XYZ"},
    "conditions.side": {"zh": "参数位置", "en": "Parametric Location"},
    "conditions.side.u0": {"zh": "U 最小边界", "en": "U-min Boundary"},
    "conditions.side.u1": {"zh": "U 最大边界", "en": "U-max Boundary"},
    "conditions.side.v0": {"zh": "V 最小边界", "en": "V-min Boundary"},
    "conditions.side.v1": {"zh": "V 最大边界", "en": "V-max Boundary"},
    "conditions.side.00": {"zh": "角点 U-min / V-min", "en": "Corner U-min / V-min"},
    "conditions.side.10": {"zh": "角点 U-max / V-min", "en": "Corner U-max / V-min"},
    "conditions.side.01": {"zh": "角点 U-min / V-max", "en": "Corner U-min / V-max"},
    "conditions.side.11": {"zh": "角点 U-max / V-max", "en": "Corner U-max / V-max"},
    "conditions.side.all": {"zh": "所有控制点", "en": "All Control Points"},
    "conditions.clamp": {"zh": "夹紧相邻控制点行",
                         "en": "Clamp Adjacent Control-Point Row"},
    "conditions.clamp_tooltip": {
        "zh": "适用于完整的自然参数边界。相邻控制点行接收相同的指定位移分量与数值。",
        "en": "Applies to a complete natural parametric boundary. The adjacent control-point row "
              "receives the same prescribed displacement components and values."},
    "conditions.penalty": {"zh": "罚因子", "en": "Penalty Factor"},
    "conditions.load_kind": {"zh": "载荷类型", "en": "Load Kind"},
    "conditions.load_kind_vector": {
        "zh": "全局向量（LINE_LOAD / SURFACE_LOAD / DEAD_LOAD）",
        "en": "Global vector (LINE_LOAD / SURFACE_LOAD / DEAD_LOAD)"},
    "conditions.load_kind_pressure": {
        "zh": "参考法向压力（dead pressure）",
        "en": "Reference normal pressure (dead pressure)"},
    "conditions.variable": {"zh": "变量", "en": "Variable"},
    "conditions.pressure": {"zh": "压力（带符号标量，应力单位）",
                            "en": "Pressure (signed scalar, stress units)"},
    "conditions.normal_flip": {"zh": "翻转参考法向（normal_flip: −n̂_ref）",
                               "en": "Flip reference normal (normal_flip: −n̂_ref)"},
    "conditions.amplitude": {"zh": "分段线性幅值表（因子，无量纲）",
                             "en": "Piecewise-linear amplitude table (factor, dimensionless)"},
    "conditions.amplitude_time": {"zh": "时间 (s)", "en": "Time (s)"},
    "conditions.amplitude_factor": {"zh": "因子", "en": "Factor"},
    "conditions.initial_field": {"zh": "初始场", "en": "Initial Field"},
    "conditions.vector_displacement": {"zh": "位移 {axis}", "en": "Displacement {axis}"},
    "conditions.end": {"zh": "结束", "en": "End"},
    "conditions.confirmed": {
        "zh": "条件已记录 | 导出前会验证几何与公式兼容性。",
        "en": "Condition recorded | Geometric and formulation compatibility will be validated "
              "before export."},
    "conditions.cleared": {"zh": "已清空 {category} 编辑器；已放弃捕获的选择。",
                           "en": "Cleared the {category} editor; captured selection discarded."},
    "conditions.group_missing": {"zh": "分组 {group}（缺失）", "en": "group {group} (missing)"},
    "conditions.group_label": {"zh": "分组 {group} \"{name}\" → {count} 个成员",
                               "en": "group {group} \"{name}\" → {count} members"},
    "conditions.group_members_missing": {"zh": "（缺失 {missing}）", "en": " (missing {missing})"},
    "conditions.table_points": {"zh": " · 表 {count} 点", "en": " · table {count} pts"},
    "conditions.constant_one": {"zh": " · 常数 1", "en": " · constant 1"},
    "conditions.pending_rebind_suffix": {"zh": " · 待重绑定", "en": " · PENDING REBIND"},

    # -- interfaces page --------------------------------------------------
    "interfaces.table.edge": {"zh": "边", "en": "Edge"},
    "interfaces.table.patches": {"zh": "相邻曲面片", "en": "Adjacent Patches"},
    "interfaces.table.state": {"zh": "状态", "en": "State"},
    "interfaces.table.source": {"zh": "来源", "en": "Source"},
    "interfaces.table.type": {"zh": "类型", "en": "Type"},
    "interfaces.table.penalty": {"zh": "罚因子", "en": "Penalty Factor"},
    "interfaces.table.diagnostic": {"zh": "配对采样诊断", "en": "Sampled Pair Diagnostic"},
    "interfaces.confirm": {"zh": "确认所选接口", "en": "Confirm Selected Interface"},
    "interfaces.exclude": {"zh": "排除所选接口", "en": "Exclude Selected Interface"},
    "interfaces.reset": {"zh": "重置为候选", "en": "Reset to Candidate"},
    "interfaces.isolate": {"zh": "只显示耦合边", "en": "Coupling edges only"},
    "interfaces.isolate_restore": {"zh": "恢复先前可见性", "en": "Restore previous visibility"},
    "interfaces.start_pick": {"zh": "启动接口点选流程", "en": "Start interface picking"},
    "interfaces.pick_hint": {
        "zh": "接口点选：左键依次点选两个已存在候选接口的曲面、再点选对应耦合边，"
              "中键在该边上再次确认；表格同步高亮对应行，右侧显示连接类型与罚因子。"
              "只能选择现有候选，不会因面相邻而虚构接口。",
        "en": "Interface picking: left-click two surfaces that already share a candidate "
              "interface, then left-click the matching coupling edge and middle-click that edge "
              "to confirm. The table highlights the matching row and the right side shows the "
              "connection type and penalty factor. Only existing candidates can be chosen; "
              "adjacent faces never invent an interface."},
    "interfaces.rotation_displacement": {"zh": "仅位移", "en": "Displacement only"},
    "interfaces.rotation_both": {"zh": "位移 + 转角", "en": "Displacement + rotation"},
    "state.confirmed": {"zh": "已确认", "en": "confirmed"},
    "state.excluded": {"zh": "已排除", "en": "excluded"},
    "state.pending": {"zh": "候选", "en": "pending"},
    "origin.user": {"zh": "用户", "en": "user"},
    "origin.geometry": {"zh": "几何", "en": "geometry"},

    # -- preview page -----------------------------------------------------
    "preview.supports": {"zh": "支承（强/罚、分量、夹紧）",
                         "en": "Supports (strong / penalty, components, clamp)"},
    "preview.loads": {"zh": "载荷（XYZ 箭头、族比例/单位、零标记）",
                      "en": "Loads (XYZ arrows, family scales/units, zero marker)"},
    "preview.frame": {"zh": "背景参数坐标系（u / v / 法向）",
                      "en": "Background parameter frame (u / v / normal)"},
    "preview.materials": {"zh": "材料 / 截面图例（ViewObject 颜色）",
                          "en": "Material / Section legend (ViewObject colour)"},
    "preview.rebuild": {"zh": "重建预览", "en": "Rebuild Preview"},
    "preview.clear": {"zh": "清除预览", "en": "Clear Preview"},
    "preview.idle": {"zh": "预览空闲。启用一个图层以检查指定。",
                     "en": "Preview is idle. Enable a layer to inspect the assignment."},
    "preview.cleared": {"zh": "预览已清除，不保留任何显示对象。",
                        "en": "Preview cleared. No display object remains."},
    "preview.layers_off": {"zh": "所有预览图层均已关闭。", "en": "All preview layers are off."},
    "preview.refinement_heading": {
        "zh": "分析细化：原始 → 目标（估算；Initialize 后核实）",
        "en": "Analysis refinement: original → target (estimate; verify after Initialize)"},
    "preview.quadrature_heading": {"zh": "积分点请求（与细化、显示相互独立）",
                                   "en": "Quadrature request (separate from refinement and display)"},
    "preview.display_heading": {"zh": "显示采样（仅显示，不是积分域）",
                                "en": "Display sampling (display only; not the integration domain)"},
    "preview.legend_heading": {"zh": "材料 / 截面图例：当前精确指定",
                               "en": "Material / Section legend: exact current assignments"},
    "preview.diagnostics_heading": {"zh": "几何诊断（仅报告，不修复）",
                                    "en": "Geometry diagnostics (reported, never repaired)"},
    "preview.inactive_heading": {"zh": "非活动（在分析域之外）：不绘制物理符号",
                                 "en": "Inactive (outside the analysis domain): no physics glyph drawn"},
    "preview.frame_heading": {"zh": "背景坐标系图例（独立坐标轴）",
                              "en": "Background-frame legend (independent axes)"},
    "preview.notes_heading": {"zh": "符号说明（分量、夹紧、采样、比例）",
                              "en": "Glyph notes (components, clamp, samples, scales)"},
    "preview.notes_placeholder": {"zh": "每个符号的说明显示在此处。",
                                  "en": "Per-glyph notes appear here."},
    "preview.table.face": {"zh": "Face", "en": "Face"},
    "preview.table.active": {"zh": "Active", "en": "Active"},
    "preview.table.orig_p": {"zh": "Orig p (u,v)", "en": "Orig p (u,v)"},
    "preview.table.target_p": {"zh": "Target p (u,v)", "en": "Target p (u,v)"},
    "preview.table.spans": {"zh": "Spans u/v", "en": "Spans u/v"},
    "preview.table.orig_cp": {"zh": "Orig CP u×v", "en": "Orig CP u×v"},
    "preview.table.est_cp": {"zh": "Est. CP u×v", "en": "Est. CP u×v"},
    "preview.table.insert": {"zh": "Insert u/v", "en": "Insert u/v"},
    "preview.table.min_c": {"zh": "min C", "en": "min C"},
    "preview.yes": {"zh": "是", "en": "yes"},
    "preview.no": {"zh": "否", "en": "no"},
    "preview.unknown": {"zh": "未知", "en": "unknown"},

    # -- analysis page ----------------------------------------------------
    "analysis.solver": {"zh": "Solver", "en": "Solver"},
    "analysis.analysis_type": {"zh": "Analysis Type", "en": "Analysis Type"},
    "analysis.static": {"zh": "Static", "en": "Static"},
    "analysis.dynamic": {"zh": "Implicit Dynamic", "en": "Implicit Dynamic"},
    "analysis.linear": {"zh": "Linear", "en": "Linear"},
    "analysis.nonlinear": {"zh": "Nonlinear", "en": "Nonlinear"},
    "analysis.start_time": {"zh": "Start Time", "en": "Start Time"},
    "analysis.end_time": {"zh": "End Time", "en": "End Time"},
    "analysis.time_step": {"zh": "Time Step", "en": "Time Step"},
    "analysis.vtk_output": {"zh": "Native VTKHDF result output", "en": "Native VTKHDF result output"},
    "analysis.output_refinement": {"zh": "Output Refinement", "en": "Output Refinement"},
    "analysis.initialize": {"zh": "Initialize Exported Model", "en": "Initialize Exported Model"},
    "analysis.run": {"zh": "Run Exported Analysis", "en": "Run Exported Analysis"},
    "analysis.export_geometry": {"zh": "Export CAD Geometry (mm)", "en": "Export CAD Geometry (mm)"},
    "analysis.backend_python": {"zh": "Backend Python", "en": "Backend Python"},
    "analysis.pipeline_root": {"zh": "Pipeline Root", "en": "Pipeline Root"},
    "analysis.work_dir": {"zh": "Work Directory", "en": "Work Directory"},
    "analysis.save_backend": {"zh": "Save Backend Configuration", "en": "Save Backend Configuration"},
    "analysis.check_backend": {"zh": "Check Backend Environment", "en": "Check Backend Environment"},
    "analysis.run_report_heading": {"zh": "Run report", "en": "Run report"},
    "analysis.run_report_heading_own": {"zh": "本次运行的结果（只读报告）",
                                        "en": "Result of this run only (read-only report)"},
    "analysis.save_to_document": {"zh": "Save to Document", "en": "Save to Document"},
    "analysis.restore_document": {"zh": "Restore Document", "en": "Restore Document"},
    "analysis.document_hint": {
        "zh": "Save to Document 通过原生保存观察者把已提交项目写入打开的 .FCStd。"
              "Restore Document 是文档入口；Open JSON 仍是独立、显式的入口——"
              "绝不按时间戳静默选择。",
        "en": "Save to Document writes the committed project into the open .FCStd through the native "
              "save observer. Restore Document is the document entry point; Open JSON stays a "
              "separate, explicit entry point - they are never chosen silently by timestamp."},
    "analysis.backend_python_label": {"zh": "后端 Python 可执行文件",
                                      "en": "Backend Python Executable"},
    "analysis.pipeline_root_label": {"zh": "转换管线目录",
                                     "en": "Conversion Pipeline Directory"},
    "analysis.work_dir_label": {"zh": "工作目录", "en": "Working Directory"},
    "analysis.export_hint": {
        "zh": "导出 CAD 几何、细化、本构属性、边界条件与求解器配置。"
              "后端：Kratos 10.4.3 及所需结构分析应用。",
        "en": "Export CAD geometry, refinement, constitutive properties, boundary conditions and "
              "solver configuration. Backend: Kratos 10.4.3 with the required structural analysis "
              "applications."},
    "analysis.export_geometry": {"zh": "仅导出 CAD 几何", "en": "Export CAD Geometry Only"},
    "analysis.stop_task": {"zh": "停止后台任务", "en": "Stop Background Task"},
    "analysis.run_dir_none": {"zh": "运行目录：暂无。", "en": "Run directory: none yet."},
    "analysis.no_report": {"zh": "没有已初始化的报告。请先导出模型并点击 Initialize Exported Model。",
                           "en": "No initialized report. Export a model and press Initialize "
                                 "Exported Model."},
    "analysis.run_report_placeholder": {
        "zh": "每次 Initialize/Run 都会在导出旁写入新的隔离运行目录，只复制已导出的输入；"
              "此处显示本次运行的节点、活动节点、DOF 总数、积分计数、"
              "每个曲面片的已初始化次数/控制点/面积以及 area_check 状态。"
              "报告不携带的字段显示为未知。",
        "en": "Each Initialize/Run writes a new isolated run directory beside the export and copies "
              "only the exported inputs; this run's nodes, active nodes, DOF totals, integration "
              "counts, per-patch initialized degree/control points/area and the area_check status "
              "appear here. Fields the report does not carry are shown as unknown."},

    # -- result page ------------------------------------------------------
    "result.run_dir": {"zh": "Run Directory", "en": "Run Directory"},
    "result.load_metadata": {"zh": "Load Result Metadata", "en": "Load Result Metadata"},
    "result.use_last": {"zh": "Use Last Run", "en": "Use Last Run"},
    "result.browse": {"zh": "Browse…", "en": "Browse…"},
    "result.step": {"zh": "Step", "en": "Step"},
    "result.scale": {"zh": "Scale", "en": "Scale"},
    "result.component": {"zh": "Component", "en": "Component"},
    "result.draw_frame": {"zh": "Draw Result Frame", "en": "Draw Result Frame"},
    "result.clear": {"zh": "Clear Result Display", "en": "Clear Result Display"},
    "result.none_loaded": {"zh": "尚未加载结果。请选择运行目录并点击加载。",
                           "en": "No result is loaded. Choose a run directory and press Load."},
    "result.metadata_loaded": {
        "zh": "已加载结果元数据；点击绘制结果帧以显示一个已校验的帧。",
        "en": "Result metadata loaded; press Draw Result Frame to display a validated frame."},
    "result.magnitude": {"zh": "magnitude", "en": "magnitude"},
    "result.component_axis": {"zh": "{axis}", "en": "{axis}"},
    "result.heading": {"zh": "结果显示 · 通过后端 Python 读取原生 results.vtkhdf",
                       "en": "Result display · native results.vtkhdf read through the backend Python"},
    "result.run_dir_tooltip": {
        "zh": "一个隔离的运行目录。默认是本面板最后一次运行；也可选择导出旁较早的独立运行目录。",
        "en": "One isolated run directory. The default is this panel's last run; older independent "
              "run directories beside the export can be selected as well."},
    "result.step_tooltip": {
        "zh": "来自 VTKHDF Steps/Values 块的真实记录时间步索引；步之间绝不插值。",
        "en": "The real recorded time-step index from the VTKHDF Steps/Values block; steps are never "
              "interpolated."},
    "result.component_tooltip": {"zh": "显示位移分量或幅值。",
                                 "en": "Displayed displacement component or magnitude."},
    "result.scale_tooltip": {
        "zh": "仅缩放所选步的位移；0 恢复参考采样几何。",
        "en": "Multiplies only the selected step's displacement; 0 restores the reference sampling "
              "geometry."},
    "result.cleared": {"zh": "结果显示已清除。", "en": "Result display cleared."},
    "result.mapping_note": {
        "zh": "face/UV 不可用：纯 VTKHDF 不携带 face/UV 映射，因此绘制的原生采样单元不是裁剪积分域，"
              "绘制帧也不是验证。帧通过后端 Python 读取，绝不写入磁盘。",
        "en": "face/UV unavailable: a plain VTKHDF carries no face/UV mapping, so the drawn native "
              "sampling cells are NOT the trimmed integration domain and a drawn frame is not a "
              "verification. The frame is read through the backend Python and is never written to "
              "disk."},
    "result.metadata_placeholder": {
        "zh": "原生 results.vtkhdf 元数据：加载结果元数据后，此处显示案例阶段、关联、点/单元计数"
              "和真实记录的步时间。",
        "en": "Native results.vtkhdf metadata: case phase, association, point/cell counts and the "
              "real recorded step times appear here after Load Result Metadata."},
    "result.extrema_placeholder": {
        "zh": "绘制结果帧后，此处显示所显示分量的极值及其真实位置。",
        "en": "Extrema of the displayed component with their real positions appear here after Draw "
              "Result Frame."},
    "result.evidence_placeholder": {
        "zh": "静态验证、初始化、求解与参考案例证据分别列出；只有自身证据存在时才显示某一级别。",
        "en": "Static validation, initialization, solve and reference-case evidence are listed "
              "independently; a level is only shown when its own evidence exists."},
    "result.forces_placeholder": {
        "zh": "来自 run_report.json 的分离力/平衡证据：强支承反力、施加载荷与原始无符号罚贡献，"
              "各自带状态。绝不虚构罚物理反力。",
        "en": "Separated force / equilibrium evidence from run_report.json: strong support reactions, "
              "applied loads and the raw, unsigned penalty contributions, each with its own status. "
              "Penalty physics reactions are never invented."},

    # -- status / dialog messages -----------------------------------------
    "msg.task_running": {"zh": "后台任务进行中…", "en": "Background task in progress…"},
    "msg.task_done": {"zh": "已完成", "en": "Completed"},
    "msg.task_failed": {"zh": "后台任务失败或被停止。请检查日志。",
                        "en": "Background task failed or was stopped. Inspect the log."},
    "msg.geometry_imported": {
        "zh": "几何已导入 | 请定义材料属性与分析条件。",
        "en": "Geometry imported | Define material properties and analysis conditions."},
    "msg.operation_failed": {"zh": "操作失败：{error}", "en": "Operation failed: {error}"},
    "msg.project_saved": {"zh": "项目已保存：{path}", "en": "Project saved: {path}"},
    "msg.exported": {"zh": "分析模型已导出：{path}", "en": "Analysis model exported: {path}"},
    "msg.geometry_exported": {"zh": "CAD 几何已导出（mm；不含分析设置）：{path}",
                              "en": "CAD geometry exported (mm; analysis settings excluded): {path}"},
    "msg.backend_saved": {"zh": "后端配置已保存。", "en": "Backend configuration saved."},
    "msg.backend_verified": {"zh": "后端环境已验证。", "en": "Backend environment verified."},
    "msg.init_done": {"zh": "模型初始化完成：{run_id} · {run_dir}",
                      "en": "Model initialization completed: {run_id} · {run_dir}"},
    "msg.validation_summary": {"zh": "错误：{errors} | 提示：{notices}",
                               "en": "Errors: {errors} | Notices: {notices}"},
    "msg.import_failed": {"zh": "导入失败：{error}", "en": "Import failed: {error}"},
    "msg.user_cancelled": {"zh": "已由用户取消。", "en": "Cancelled by the user."},
    "msg.esc_unavailable": {"zh": "（Esc 快捷键不可用：{error}）",
                            "en": " (Esc shortcut unavailable: {error})"},
    "msg.interaction_hook_unavailable": {"zh": "工作台退出清理挂钩不可用：{error}",
                                         "en": "Workbench-deactivation cleanup hook unavailable: {error}"},    "msg.pick_read_failed": {"zh": "无法把该曲面子边唯一映射到已注册的单侧边界或候选耦合边；"
                                   "请改用几何表，或直接点选独立的 Edge / Coupling 边对象。",
                             "en": "This surface sub-edge cannot be mapped uniquely to a registered "
                                   "one-sided boundary or candidate coupling edge; use the geometry "
                                   "table or pick the standalone Edge / Coupling object instead."},

    # -- live status messages (each keeps its parameters as data) ----------
    "msg.snapshot_converting": {"zh": "正在用现有 worker 转换快照…",
                                "en": "Converting the snapshot with the existing worker..."},
    "msg.snapshot_captured": {"zh": "已捕获快照：写出 {faces} 个面到 {name}。"
                                    "请转换，然后解析下面的每个映射。",
                              "en": "Snapshot captured: {faces} face(s) written to {name}. "
                                    "Convert it, then resolve every mapping below."},
    "msg.snapshot_converted": {"zh": "已转换：{faces} 个新面。预览文档 '{preview}' 显示每个新面/边"
                                      "及其类型与 ID；{required} 个旧目标必须映射或放弃。尚未更改任何内容。",
                               "en": "Converted: {faces} new face(s). Preview document '{preview}' "
                                     "shows every new face/edge with its kind and ID; {required} old "
                                     "target(s) must be mapped or dropped. Nothing has been changed yet."},
    "msg.snapshot_mapping_cancelled": {"zh": "映射已取消；未更改任何内容。",
                                       "en": "Mapping cancelled; nothing was changed."},
    "msg.snapshot_applied": {"zh": "映射已应用。{defaults} 个新面获得默认曲面片设置；"
                                   "{couplings} 个接口重新生成为候选；原文档保持打开且未更改。",
                             "en": "Mapping applied. {defaults} new face(s) received default patch "
                                   "settings; {couplings} interface(s) regenerated as pending; the "
                                   "previous document remains open and unchanged."},
    "msg.snapshot_applied_short": {"zh": "受控快照已应用；原文档保持打开且未更改。",
                                   "en": "Controlled snapshot applied; the previous document remains "
                                         "open and unchanged."},
    "msg.snapshot_cancelled": {"zh": "快照已取消；原文档、源、缓存与每个选择均未更改。",
                               "en": "Snapshot cancelled; the original document, source, cache and "
                                     "every choice are unchanged."},
    "msg.snapshot_recovered": {"zh": "已在待处理状态恢复存储的项目（保留 SyncError）。"
                                     "请捕获受控快照以显式重映射。",
                               "en": "Recovered the stored project in a pending state (SyncError "
                                     "kept). Capture a controlled snapshot to remap explicitly."},
    "msg.group_created": {"zh": "已从选择创建组：{kind} {members}",
                          "en": "Group created from selection: {kind} {members}"},
    "msg.bulk_applied": {"zh": "已为 {count} 个曲面片更新材料、公式与细化。",
                         "en": "Material, formulation and refinement updated for {count} patches."},
    "msg.select_interface_row": {"zh": "请先选择接口行。", "en": "Select an interface row first."},
    "msg.interface_state": {"zh": "接口 {edge}：{state}（来源：{origin}）。",
                            "en": "Interface {edge}: {state} (source: {origin})."},
    "msg.no_run_dir": {"zh": "尚无可用运行目录；请先点击 Initialize 或 Run。",
                       "en": "No run directory is available yet; press Initialize or Run first."},
    "msg.result_last_run": {"zh": "结果运行目录已设为最后一次运行：{run}",
                            "en": "Result run directory set to the last run: {run}"},
    "msg.result_run_selected": {"zh": "已选择结果运行目录：{run}",
                                "en": "Result run directory selected: {run}"},
    "msg.result_error": {"zh": "结果错误：{error}", "en": "Result error: {error}"},
    "msg.result_metadata_loaded": {"zh": "已加载结果元数据；点击 Draw Result Frame 显示一个已校验的帧。",
                                   "en": "Result metadata loaded; press Draw Result Frame to display "
                                         "a validated frame."},
    "msg.result_cleared": {"zh": "结果显示已清除。", "en": "Result display cleared."},
    "msg.run_dir_none": {"zh": "运行目录：暂无。", "en": "Run directory: none yet."},
    "msg.run_dir": {"zh": "运行目录：{folder}", "en": "Run directory: {folder}"},
    "msg.no_report_yet": {"zh": "本次运行尚无 run_report.json（{folder}）；请点击 "
                                "Initialize Exported Model 或 Run Exported Analysis。",
                          "en": "No run_report.json in this run yet ({folder}); press Initialize "
                                "Exported Model or Run Exported Analysis."},
    "msg.unreadable_report": {"zh": "无法读取 {folder} 中的 {source}：{error}",
                              "en": "Unreadable {source} in {folder}: {error}"},
    "msg.export_only": {"zh": "仅导出案例（本次运行未生成报告）：{folder} · {source}；"
                              "初始化统计未知",
                        "en": "Exported case only (this run produced no report): {folder} · "
                              "{source}; initialized statistics are unknown"},
    "msg.initialized_run": {"zh": "已初始化运行：{folder} · {source} · mode={mode}（求解前）",
                            "en": "Initialized run: {folder} · {source} · mode={mode} (pre-solve)"},
    "msg.solved_run": {"zh": "已求解运行：{folder} · {source} · mode={mode}；"
                             "面积自 Initialize 起冻结",
                       "en": "Solved run: {folder} · {source} · mode={mode}; area frozen from "
                             "Initialize"},
    "msg.run_report": {"zh": "运行报告：{folder} · {source} · mode={mode}",
                       "en": "Run report: {folder} · {source} · mode={mode}"},
    "msg.no_initialized_report": {"zh": "没有已初始化的报告。请导出模型并点击 Initialize Exported Model。",
                                  "en": "No initialized report. Export a model and press "
                                        "Initialize Exported Model."},
    "msg.preview_idle": {"zh": "预览空闲。启用一个图层以检查指定。",
                         "en": "Preview is idle. Enable a layer to inspect the assignment."},
    "msg.preview_open_document": {"zh": "请打开文档并导入几何以预览。",
                                  "en": "Open a document and import geometry to preview."},
    "msg.preview_import": {"zh": "请导入 STEP 几何以预览预处理。",
                           "en": "Import STEP geometry to preview the preprocessing."},
    "msg.preview_geometry_blocked": {"zh": "预览被文档几何/源诊断阻止：{detail}",
                                     "en": "Preview blocked by the document geometry/source "
                                           "diagnostic: {detail}"},
    "msg.preview_no_cad": {"zh": "缓存 geometry.cad.json 不可用；请重新导入源 STEP。",
                           "en": "The cached geometry.cad.json is unavailable; reimport the source "
                                 "STEP."},
    "msg.preview_layers_off": {"zh": "所有预览图层均已关闭。", "en": "All preview layers are off."},
    "msg.preview_cleared": {"zh": "预览已清除，不保留任何显示对象。",
                            "en": "Preview cleared. No display object remains."},
    "msg.preview_diagnostic": {"zh": "预览被有效诊断阻止：{detail}",
                               "en": "Preview blocked by a valid diagnostic: {detail}"},
    "msg.workbench_hook_unavailable": {"zh": "工作台退出清理挂钩不可用：{error}",
                                       "en": "Workbench-deactivation cleanup hook unavailable: {error}"},
    "msg.geometry_changed": {"zh": "几何已变化：每个绑定都待重绑定。",
                             "en": "Geometry changed: every binding is pending rebind."},
    "msg.geometry_changed_rebind": {
        "zh": "几何已变化：每个绑定都待重绑定。请在导出前到“分组”页解决。",
        "en": "Geometry changed: every binding is pending rebind. Resolve them in Groups before "
              "exporting."},
    "msg.already_units": {"zh": "项目已使用 {target}。", "en": "The project already uses {target}."},
    "msg.units_converted": {"zh": "已把所有物理值从原单位体系转换为 {target}。",
                            "en": "Converted all physical values from the previous unit system to "
                                  "{target}."},
    "msg.doc_association_failed": {"zh": "文档关联失败：{error}",
                                   "en": "Document association failed: {error}"},
    "msg.project_written": {"zh": "项目已写入 FreeCAD 文档。",
                            "en": "Project written into the FreeCAD document."},
    "msg.project_written_sync": {"zh": "项目已写入 FreeCAD 文档。{error}",
                                 "en": "Project written into the FreeCAD document.{error}"},
    "msg.sync_error": {"zh": " 同步错误：{error}", "en": " Sync error: {error}"},
    "msg.summary_line": {"zh": "{name}   |   面片：{patches}   |   界面：{interfaces}",
                         "en": "{name}   |   Patches: {patches}   |   Interfaces: {interfaces}"},
    "msg.units_pending": {"zh": "   |   目标：{target}（尚未应用）",
                          "en": "   |   Selected target: {target} (NOT applied)"},
    "msg.corner_suffix": {"zh": " · 角点 {side}", "en": " · corner {side}"},
    "msg.result_cleanup": {"zh": "结果显示清理报告：{error}",
                           "en": "Result display cleanup reported: {error}"},
    "dialog.rename_group": {"zh": "重命名分组", "en": "Rename Group"},
    "dialog.name": {"zh": "名称", "en": "Name"},
    "dialog.conversion_profile": {"zh": "转换配置", "en": "Conversion Profile"},
    "dialog.import_step": {"zh": "导入 STEP", "en": "Import STEP"},
    "dialog.save_document": {"zh": "保存 FreeCAD 文档", "en": "Save FreeCAD Document"},
    "dialog.result_run_dir": {"zh": "选择运行目录", "en": "Select run directory"},
    "dialog.save_project": {"zh": "保存分析项目", "en": "Save Analysis Project"},
    "dialog.open_project": {"zh": "打开分析项目", "en": "Open Analysis Project"},
    "dialog.export_parent": {"zh": "选择新分析模型的父目录",
                             "en": "Select Parent Directory for a New Analysis Model"},
    "dialog.export_geometry": {"zh": "导出源 CAD 几何（mm）",
                               "en": "Export Source CAD Geometry (mm)"},
    "msg.analysis_done": {"zh": "分析已完成：{run_id} · {run_dir}",
                          "en": "Analysis completed: {run_id} · {run_dir}"},
    "restore.cache_reused": {
        "zh": "已从保存的缓存恢复文档；现有对象、分组与实体关联被复用。{pending}",
        "en": "Document restored from its saved cache; existing objects, groups and the entity "
              "association were reused.{pending}"},
    "restore.pending_suffix": {"zh": "部分绑定待重绑定。", "en": " Some bindings are pending rebind."},
    "restore.rebuild_mode": {
        "zh": "以重建模式恢复；目标文档被复用，修改过的形状绝不会被覆盖。",
        "en": "Restoring in rebuild mode; the intended document is reused and modified shapes are "
              "never overwritten."},
    "log.converted_chunk": {"zh": "转换输出", "en": "conversion output"},
    "msg.preview_scales": {"zh": "{scales}。", "en": "{scales}."},
    "msg.preview_no_scale": {"zh": "无载荷比例", "en": "no load scale"},
    "msg.preview_source": {"zh": "来源：{source}。", "en": "Source: {source}."},
    "msg.preview_inactive_targets": {"zh": "{count} 个非活动目标没有符号。",
                                     "en": "{count} inactive target(s) have no glyph."},
    "msg.preview_summary": {"zh": "预览：{supports} 支承 · {loads} 载荷 · {frames} 框架轴对象；"
                                  "{materials} 个面已重新着色。",
                            "en": "Preview: {supports} support · {loads} load · {frames} frame axis "
                                  "object(s); {materials} face(s) recoloured."},
    # -- mouse interaction guidance, refusals and audit -------------------
    "interaction.targets.empty": {"zh": "（空）", "en": "(empty)"},
    "interaction.kind.face": {"zh": "Patch", "en": "Patch"},
    "interaction.kind.edge": {"zh": "Edge", "en": "Edge"},
    "interaction.kind.coupling": {"zh": "Coupling", "en": "Coupling"},
    "interaction.corner": {"zh": " （角点 {side}）", "en": " (corner {side})"},
    "interaction.hover_pair": {"zh": "Patch {first} ↔ Patch {second}",
                               "en": "Patch {first} ↔ Patch {second}"},
    "interaction.hover.corner": {"zh": "角点 {side}", "en": "corner {side}"},
    "interaction.notice_prefix": {"zh": "提示：{text}", "en": "Notice: {text}"},
    "interaction.hover_prefix": {"zh": "悬浮：{text}", "en": "Hover: {text}"},
    "interaction.hover.hidden_kind": {"zh": "（当前查看模式不显示该类型）",
                                      "en": " (this type is hidden by the current hover mode)"},
    "interaction.category.supports": {"zh": "支承", "en": "Support"},
    "interaction.category.loads": {"zh": "载荷", "en": "Load"},
    "interaction.category.initial_conditions": {"zh": "初始场", "en": "Initial field"},
    "interaction.allowed.initial": {"zh": "仅面 Patch", "en": "face Patch only"},
    "interaction.allowed.pressure": {"zh": "仅面 Patch（参考法向压力）",
                                     "en": "face Patch only (reference normal pressure)"},
    "interaction.allowed.loads": {"zh": "面 Patch 或单侧边界 Edge",
                                  "en": "face Patch or one-sided boundary Edge"},
    "interaction.allowed.supports": {"zh": "面 Patch、单侧边界 Edge 或自然角点",
                                     "en": "face Patch, one-sided boundary Edge or natural corner"},

    "interaction.guidance.idle": {"zh": GUIDANCE_IDLE_SOURCE, "en":
        "Mouse picking: on the Boundary / Initial page press Start physics picking, "
        "left-click geometry, middle-click to confirm and press Esc to cancel; hover "
        "shows the registered entity ID."},
    "interaction.guidance.need_start": {
        "zh": "尚未启动点选：请先点击“启动物理点选”或“启动接口点选流程”。",
        "en": "Picking has not started: press Start physics picking or Start interface picking first."},
    "interaction.guidance.started": {
        "zh": "{label}点选已启动：左键点选目标（{allowed}），中键确认当前选择，Esc 取消。",
        "en": "{label} picking started: left-click targets ({allowed}), middle-click to confirm the "
              "current selection, Esc cancels."},
    "interaction.guidance.started_coupling": {
        "zh": "接口点选已启动：左键点选第一个候选耦合面，Esc 取消。",
        "en": "Interface picking started: left-click the first candidate coupling face, Esc cancels."},
    "interaction.guidance.selected": {
        "zh": "已选：{items}。可继续左键点选或中键确认。",
        "en": "Selected: {items}. Keep left-clicking or middle-click to confirm."},
    "interaction.guidance.confirmed": {
        "zh": "目标已确认：请在右侧 {label} 表单填写参数，然后点击 Add / Update 提交；"
              "中键只在点选过程中被占用，正常视图导航不受影响。",
        "en": "Target confirmed: fill in the {label} form on the right, then press Add / Update to "
              "submit. The middle button is owned only during picking; normal view navigation is "
              "unaffected."},
    "interaction.guidance.confirmed_pressure": {
        "zh": "目标已确认：请在右侧 {label} 表单填写参数，然后点击 Add / Update 提交；"
              "中键只在点选过程中被占用，正常视图导航不受影响。",
        "en": "Target confirmed: fill in the {label} form on the right, then press Add / Update to "
              "submit. The middle button is owned only during picking; normal view navigation is "
              "unaffected."},
    "interaction.guidance.coupling_face2": {
        "zh": "左键点选另一个候选耦合面（必须与第一个面存在现有候选接口）。",
        "en": "Left-click the other candidate coupling face (it must already share a candidate "
              "interface with the first face)."},
    "interaction.guidance.coupling_edge": {
        "zh": "左键点选对应的候选耦合边（Coupling {edge}）。",
        "en": "Left-click the matching candidate coupling edge (Coupling {edge})."},
    "interaction.guidance.coupling_edge_multi": {
        "zh": "两个面之间存在多条候选接口（Coupling {edges}）：请左键点选你们要使用的那一条。",
        "en": "Several candidate interfaces exist between the two faces (Coupling {edges}): "
              "left-click the one you intend to use."},
    "interaction.guidance.coupling_edge_first": {
        "zh": "必须先用左键点选该候选耦合边（Coupling {edge}），再用中键确认。",
        "en": "You must left-click that candidate coupling edge (Coupling {edge}) before "
              "middle-clicking to confirm."},
    "interaction.guidance.coupling_middle": {
        "zh": "中键在耦合边 Coupling {edge} 上再次点选以确认目标。",
        "en": "Middle-click the coupling edge Coupling {edge} again to confirm the target."},
    "interaction.guidance.coupling_decided": {
        "zh": "接口 Coupling {edge} 目标已确认；右侧已显示连接类型与罚因子。"
              "请点击 Confirm Selected Interface 或 Exclude Selected Interface 显式保存连接意图。",
        "en": "Interface Coupling {edge} target confirmed; the connection type and penalty factor "
              "are shown on the right. Press Confirm Selected Interface or Exclude Selected "
              "Interface to save the connection intent explicitly."},
    "interaction.guidance.cancelled": {"zh": "已取消点选，临时回调和临时显示已清除。",
                                       "en": "Picking cancelled; temporary callbacks and temporary "
                                             "display were cleared."},
    "interaction.guidance.page_switch": {
        "zh": "已切换页面：临时回调和临时显示已清除；回到页面可重新启动点选。",
        "en": "Page switched: temporary callbacks and temporary display were cleared; return to the "
              "page to start picking again."},
    "interaction.guidance.import": {
        "zh": "几何已重新导入：临时点选状态和临时显示已清除。",
        "en": "Geometry reimported: the temporary picking state and temporary display were cleared."},
    "interaction.guidance.doc_closed": {
        "zh": "文档已关闭：临时回调和临时显示已清除。",
        "en": "Document closed: temporary callbacks and temporary display were cleared."},
    "interaction.guidance.isolated": {
        "zh": "已只显示耦合边（{count} 条）；再次点击可恢复先前的可见性。",
        "en": "Showing coupling edges only ({count}); press again to restore the previous visibility."},
    "interaction.guidance.restored": {"zh": "已恢复先前可见性；分析域与接口决策未改变。",
                                      "en": "Previous visibility restored; the analysis domain and "
                                            "interface decisions are unchanged."},
    "interaction.guidance.committing": {"zh": "正在把中键确认的目标写入编辑器…",
                                        "en": "Writing the middle-confirmed target into the editor…"},
    "interaction.guidance.capture_failed": {
        "zh": "目标未被写入编辑器：捕获被拒绝或结果不匹配，未确认任何条件；请检查右侧提示后重新点选。",
        "en": "The target was not written into the editor: the capture was refused or did not match, "
              "so no condition was confirmed. Check the message on the right and pick again."},
    "interaction.notice.revealed": {"zh": "已在点选期间临时显示原隐藏的接口 {edge}；取消或切换选择后恢复。",
                                    "en": "Hidden interface {edge} is shown temporarily during "
                                          "picking; it is restored on cancel or selection change."},
    "interaction.notice.isolate_skipped": {
        "zh": "（{count} 个对象不可用，已跳过并记录）",
        "en": " ({count} object(s) were unavailable and were skipped and logged)"},
    "interaction.notice.capture_failed": {"zh": "捕获未写入目标：{targets}",
                                          "en": "Capture did not write the target(s): {targets}"},

    "interaction.refusal.foreign_document": {
        "zh": "该对象不属于当前项目文档，已拒绝；选择状态未改动。",
        "en": "That object does not belong to the current project document; refused and the "
              "selection state is unchanged."},
    "interaction.refusal.preview_or_result": {
        "zh": "预览/结果对象不可作为分析目标，已拒绝；选择状态未改动。",
        "en": "A preview/result object cannot be an analysis target; refused and the selection "
              "state is unchanged."},
    "interaction.refusal.unregistered": {
        "zh": "该显示对象未注册为分析实体（缺少 IbraBrepId），已拒绝；选择状态未改动。",
        "en": "That display object is not registered as an analysis entity (no IbraBrepId); "
              "refused and the selection state is unchanged."},
    "interaction.refusal.bad_kind": {
        "zh": "不支持的实体类型 {kind}，已拒绝；选择状态未改动。",
        "en": "Unsupported entity kind {kind}; refused and the selection state is unchanged."},
    "interaction.refusal.inconsistent_tag": {
        "zh": "实体的 IbraBrepId/IbraKind 与注册表不一致，已拒绝；选择状态未改动。",
        "en": "The entity's IbraBrepId/IbraKind disagrees with the registry; refused and the "
              "selection state is unchanged."},
    "interaction.refusal.no_fingerprint": {
        "zh": "该实体缺少显示基线指纹，无法确认几何未变化；已拒绝且未改动状态。",
        "en": "That entity has no display baseline fingerprint, so unchanged geometry cannot be "
              "confirmed; refused with no state change."},
    "interaction.refusal.geometry_changed": {
        "zh": "该实体的几何或位置已变化，指纹失效；请重新导入后重试，状态未改动。",
        "en": "That entity's geometry or position changed and the fingerprint is stale; reimport "
              "and try again. No state was changed."},
    "interaction.refusal.one_subelement": {
        "zh": "一次只能解析一个子元素；已拒绝且未改动状态。",
        "en": "Only one sub-element can be resolved at a time; refused with no state change."},
    "interaction.refusal.ambiguous_subedge": {
        "zh": "无法唯一解析该曲面上的子边；请改用几何表或独立边界对象。",
        "en": "This surface sub-edge cannot be resolved uniquely; use the geometry table or a "
              "standalone boundary object instead."},
    "interaction.refusal.hover_mode": {
        "zh": "未知的查看模式 {mode}；可选 {modes}。",
        "en": "Unknown hover mode {mode}; choose one of {modes}."},
    "interaction.refusal.category": {"zh": "未知的条件类别 {category}。",
                                     "en": "Unknown condition category {category}."},
    "interaction.refusal.coupling_flow_face": {
        "zh": "接口流程要求先左键点选候选曲面片。",
        "en": "The interface flow requires left-clicking a candidate surface first."},
    "interaction.refusal.coupling_same_face": {
        "zh": "第二个面必须与第一个面不同；状态未改动。",
        "en": "The second face must differ from the first; no state was changed."},
    "interaction.refusal.no_existing_candidate": {
        "zh": "这两个面之间没有现有候选接口；不会因相邻而虚构接口，状态未改动。",
        "en": "These two faces share no existing candidate interface; adjacency never invents one "
              "and no state was changed."},
    "interaction.refusal.coupling_edge_required": {
        "zh": "请点选接口边（Coupling），而不是普通边界；状态未改动。",
        "en": "Pick the interface edge (Coupling), not an ordinary boundary; no state was changed."},
    "interaction.refusal.wrong_coupling_edge": {
        "zh": "点选的耦合边与两个面之间的现有候选不一致；状态未改动。",
        "en": "The picked coupling edge does not match the existing candidate between the two "
              "faces; no state was changed."},
    "interaction.refusal.coupling_state": {
        "zh": "接口点选状态异常，请按 Esc 取消后重新启动。",
        "en": "The interface picking state is inconsistent; press Esc to cancel and start again."},
    "interaction.refusal.no_document": {
        "zh": "尚未关联项目文档：请先导入几何或恢复文档。",
        "en": "No project document is associated yet: import geometry or restore a document first."},
    "interaction.refusal.no_target": {"zh": "请先用左键点选目标，再用中键确认。",
                                      "en": "Left-click a target first, then middle-click to confirm."},
    "interaction.refusal.middle_mismatch": {
        "zh": "中键确认必须与当前左键所选目标一致；状态未改动。",
        "en": "The middle-click confirmation must match the currently left-selected target; no "
              "state was changed."},
    "interaction.refusal.middle_mismatch_coupling": {
        "zh": "中键确认必须在同一条候选耦合边上；状态未改动。",
        "en": "The middle-click confirmation must be on the same candidate coupling edge; no state "
              "was changed."},
    "interaction.refusal.coupling_confirm_order": {
        "zh": "请先依次左键选择两个候选面和对应耦合边，然后中键确认。",
        "en": "Left-click the two candidate faces and the matching coupling edge in order before "
              "middle-clicking to confirm."},
    "interaction.refusal.coupling_as_boundary": {
        "zh": "接口边不得用作普通边界/初始条件目标；状态未改动。",
        "en": "An interface edge cannot be an ordinary boundary/initial-condition target; no state "
              "was changed."},
    "interaction.refusal.unsupported_kind": {
        "zh": "不支持的实体类型 {kind}；状态未改动。",
        "en": "Unsupported entity kind {kind}; no state was changed."},
    "interaction.refusal.initial_face_only": {"zh": "初始场仅允许面目标；状态未改动。",
                                              "en": "An initial field accepts face targets only; no "
                                                    "state was changed."},
    "interaction.refusal.pressure_face_only": {
        "zh": "参考法向压力仅允许面目标；状态未改动。",
        "en": "Reference normal pressure accepts face targets only; no state was changed."},
    "interaction.refusal.initial_whole_face": {
        "zh": "初始场只接受完整曲面片，不接受角点/自然边子元素；状态未改动。",
        "en": "An initial field accepts a whole surface patch only, not a corner/natural-edge "
              "sub-element; no state was changed."},
    "interaction.refusal.pressure_whole_face": {
        "zh": "参考法向压力只接受完整曲面片，不接受角点/自然边子元素；状态未改动。",
        "en": "Reference normal pressure accepts a whole surface patch only, not a "
              "corner/natural-edge sub-element; no state was changed."},

    "interaction.log.doc_observer_failed": {"zh": "文档关闭观察者注册失败：{error}",
                                            "en": "Document-close observer registration failed: {error}"},
    "interaction.log.doc_observer_remove_failed": {"zh": "文档关闭观察者移除失败：{error}",
                                                   "en": "Document-close observer removal failed: {error}"},
    "interaction.log.callback_unavailable": {"zh": "鼠标回调不可用：{error}",
                                             "en": "Mouse callback unavailable: {error}"},
    "interaction.log.callback_cleanup": {"zh": "鼠标回调清理报告：{errors}",
                                         "en": "Mouse callback cleanup report: {errors}"},    "interaction.log.pressure_mode_failed": {"zh": "读取压力模式失败：{error}",
                                             "en": "Reading the pressure mode failed: {error}"},
    "interaction.log.isolate_skipped": {"zh": "耦合边单独显示跳过对象：",
                                        "en": "Coupling-only display skipped objects: "},
    "interaction.log.restore_skipped": {"zh": "恢复可见性时跳过不可用对象：",
                                        "en": "Restoring visibility skipped unavailable objects: "},
    "interaction.log.reveal_restore_failed": {"zh": "恢复表格临时显示的接口失败：",
                                              "en": "Restoring the table-revealed interface failed: "},
    "interaction.log.observer_pause_failed": {"zh": "暂停 Selection 观察者失败：{error}",
                                              "en": "Pausing the Selection observer failed: {error}"},
    "interaction.log.observer_resume_failed": {"zh": "恢复 Selection 观察者失败：{error}",
                                               "en": "Resuming the Selection observer failed: {error}"},
    "interaction.log.no_renderer": {"zh": "未找到原生 3D 渲染器控件；中键过滤未安装。",
                                    "en": "No native 3D renderer widget was found; the middle-button "
                                          "filter was not installed."},
    "interaction.log.filter_install_failed": {"zh": "中键过滤器安装失败：{target}",
                                              "en": "Middle-button filter installation failed: {target}"},
    "interaction.log.filter_remove_failed": {"zh": "中键过滤器移除失败：{target}",
                                             "en": "Middle-button filter removal failed: {target}"},
    "interaction.log.pick_failed": {"zh": "三维视图拾取失败：{error}",
                                    "en": "3D view picking failed: {error}"},
    "interaction.log.apply_failed": {"zh": "鼠标操作应用失败：{error}",
                                     "en": "Applying the mouse action failed: {error}"},
    "interaction.log.subedge_detail": {"zh": "无法把该曲面子边唯一映射到已注册的单侧边界或候选耦合边；"
                                             "请改用几何表，或直接点选独立的 Edge / Coupling 边对象。",
                                       "en": "This surface sub-edge cannot be mapped uniquely to a "
                                             "registered one-sided boundary or candidate coupling "
                                             "edge; use the geometry table or pick the standalone "
                                             "Edge / Coupling object instead."},

    # -- geometry hint texts ----------------------------------------------
    "geometry.table_hint": {
        "zh": "在表格或视图中选择曲面片或边；按住 Ctrl 可多选。橙色曲线表示候选接口。"
              "显示折线不替代 NURBS 分析几何。",
        "en": "Select patches or edges in the table or viewport; hold Ctrl for multiple entities. "
              "Orange curves indicate candidate interfaces. Display polylines do not replace the "
              "NURBS analysis geometry."},
    "geometry.no_target": {"zh": "未选择目标实体", "en": "No target entity selected"},
    "geometry.profile_hint": {
        "zh": "更改配置文件后请重新导入。面排除与重复面处理遵循已配置的转换管线。",
        "en": "Reimport after changing the profile. Face exclusion and duplicate-face handling "
              "follow the configured conversion pipeline."},
    "geometry.hover_audit_tooltip": {
        "zh": "安装 Selection 观察者以显示光标下已注册的 Edge / Patch / Coupling ID。"
              "页面切换、取消、导入、文档关闭或退出时会移除。",
        "en": "Install the Selection observer to show the registered Edge / Patch / Coupling ID "
              "under the cursor. It is removed on page switch, cancel, import, document close or "
              "exit."},
    "geometry.hover_hint": {
        "zh": "悬浮显示光标下实体的已注册 B-rep ID；审计观察者只读取状态，并随点选会话移除。",
        "en": "Hover shows the registered B-rep ID of the entity under the cursor; the audit "
              "observer only reads state and is removed with the picking session."},

    # -- materials / groups hint texts ------------------------------------
    "materials.hint": {
        "zh": "本构模型：平面应力下的各向同性线弹性。材料包含 E、ν 和 ρ；截面包含厚度，"
              "并可由多个曲面片共享。",
        "en": "Constitutive law: isotropic linear elasticity under plane stress. Materials hold E, "
              "ν and ρ. Sections hold thickness and may be shared by several patches."},
    "materials.units_hint": {
        "zh": "OCCT 以 mm 导入 STEP 坐标。m-N-s 体系把导出几何缩放 0.001。"
              "mm-N-s 中的密度以 tonne/mm³ 表示。更改单位体系会转换每个物理值，"
              "绝不静默重缩放。验证、保存与导出前必须先应用显示的目标单位。",
        "en": "OCCT imports STEP coordinates in mm. The m-N-s system scales exported geometry by "
              "0.001. Density in mm-N-s is expressed in tonne/mm³. Changing the unit system "
              "converts every physical value; it never rescales silently. Validate, save and export "
              "require the displayed target to be applied first."},
    "materials.formulation_hint": {
        "zh": "壳公式要求次数 ≥ 2 且内部 C¹ 连续。升阶保持现有 C⁰ 连续性。膜要求次数 ≥ 1。"
              "梁与体公式不在当前范围内。",
        "en": "The shell formulation requires degree ≥ 2 and internal C¹ continuity. Degree "
              "elevation preserves existing C⁰ continuity. Membranes require degree ≥ 1. Beam and "
              "volumetric formulations are outside the current scope."},
    "groups.hint": {
        "zh": "命名组保存显式的面或边 B-rep ID。条件只能指向一个组或直接目标，不能同时两者。"
              "缺失成员会显示为缺失并阻止导出——绝不静默删除。",
        "en": "Named groups hold explicit face or edge B-rep IDs. A condition either points at one "
              "group or at direct targets, never both. Missing members are shown as missing and "
              "block export - they are never silently removed."},
    "groups.accept_hint": {
        "zh": "组只接受单侧面或边界边。接口、预览折线和混合选择会被明确拒绝。",
        "en": "Groups accept one-sided faces or boundary edges only. Interfaces, preview polylines "
              "and mixed selections are rejected explicitly."},
    "groups.rebind_hint": {
        "zh": "几何变化后每个相关绑定都会被标记为待重绑。不会从偶然相等的 ID 继承任何东西："
              "请显式重绑或放弃。此列表非空时导出保持阻止。",
        "en": "After a geometry change every relevant binding is marked pending. Nothing is "
              "inherited from a coincidentally equal ID: rebind explicitly or drop it. Export stays "
              "blocked while this list is not empty."},
    "groups.snapshot_hint": {
        "zh": "快照在工作区写入新的 STEP，并用现有 worker 转换。每个必需的旧面/边都保持 "
              "UNRESOLVED——即使 ID 相等——直到你选择同类型新目标或显式放弃。"
              "只有完整映射被应用后才会创建、绑定或覆盖；取消会保持原文档、源、缓存与选择不变。",
        "en": "The snapshot writes a new STEP under the work area and converts it with the existing "
              "worker. Every required old face/edge stays UNRESOLVED - even at an equal ID - until "
              "you choose a new same-kind target or explicitly drop it. Nothing is created, bound "
              "or overwritten until a complete mapping is applied; cancelling leaves the original "
              "document, source, cache and choices unchanged."},

    # -- condition / interfaces / preview hint texts ----------------------
    "conditions.pick_start_hint": {
        "zh": "鼠标点选：左键点选已注册的 Patch / Edge，中键确认后回到本表单；"
              "初始场与参考法向压力仅接受面。Esc 取消并恢复视图。",
        "en": "Mouse picking: left-click a registered Patch / Edge and middle-click to confirm; "
              "you return to this form. Initial fields and reference normal pressure accept faces "
              "only. Esc cancels and restores the view."},
    "conditions.pressure_note": {
        "zh": "正压力沿 X0 参考法向 unit(du×dv)——这是 Kratos 参数化方向，不是 CAD OCCT 面朝向。"
              "仅为 dead/reference 压力；不支持 follower/current-normal 压力。"
              "面中点采样可能落在裁剪孔内。",
        "en": "Positive pressure follows the X0 reference normal unit(du×dv) - the Kratos "
              "parameterization, not the CAD OCCT face orientation. This is a dead/reference "
              "pressure only; follower/current-normal pressure is NOT supported. The face midpoint "
              "sample may lie in a trimming hole."},
    "conditions.support_hint": {
        "zh": "强执行在自然边界和角点上约束单个位移分量。一般裁剪曲线需要罚函数执行，"
              "目前在分析区间内限制为 XYZ。",
        "en": "Strong enforcement supports individual displacement components on natural boundaries "
              "and corners. General trimming curves require penalty enforcement, currently "
              "restricted to XYZ throughout the analysis interval."},
    "conditions.amplitude_hint": {
        "zh": "严格递增的有限时间，至少两点；线性插值；表外为零。时间以秒为单位，"
              "因子无量纲——两者都不做单位转换。预览绘制基础压力（因子 1），不暗示当前时间。",
        "en": "Strictly increasing finite times, at least two points; linear interpolation; zero "
              "outside the table. Times are seconds and the factor is dimensionless - neither is "
              "unit-converted. The preview draws the base pressure (factor 1) and never implies a "
              "current time."},
    "conditions.load_family_hint": {
        "zh": "LINE_LOAD：单位长度力。SURFACE_LOAD：单位面积力。DEAD_LOAD 在所选几何测度上积分。"
              "活动区间之外载荷为零。",
        "en": "LINE_LOAD: force per unit length. SURFACE_LOAD: force per unit area. DEAD_LOAD is "
              "integrated over the selected geometric measure. Loads are zero outside the active "
              "interval."},
    "conditions.initial_hint": {
        "zh": "初始场一次性指定到曲面的所有控制点，不约束自由度。速度与加速度需要动力分析；"
              "非零初始位移需要非线性分析。",
        "en": "Initial fields are assigned once to all control points of a patch without "
              "constraining degrees of freedom. Velocity and acceleration require dynamic analysis; "
              "nonzero initial displacement requires nonlinear analysis."},
    "interfaces.hint": {
        "zh": "检测到的接口以候选开始，绝不导出。确认你打算使用的连接模型；"
              "只有已确认接口才成为 Kratos 条件。'Source' 记录决策来自你还是未审阅的遗留默认值。"
              "配对诊断在各自源曲面上采样两条裁剪曲线：它是采样间隙与方向，不是整边重合的证明，"
              "也不是焊接或铰接模型。",
        "en": "Detected interfaces start as candidates and are never exported. Confirm the ones "
              "whose connection model you intend; only confirmed interfaces become Kratos "
              "conditions. 'Source' records whether the decision came from you or from an "
              "unreviewed legacy default. The pair diagnostic samples both trimming curves on "
              "their own source surfaces: it is a sampled gap and direction, not proof of full-edge "
              "coincidence, and it is not a weld or hinge model."},
    "preview.hint": {
        "zh": "仅显示用的预处理检查。每个符号都来自当前项目指定与缓存 CAD 曲面；"
              "绝不写 geometry.cad.json，绝不更改 Shape 或 Placement，"
              "也绝不进入分析实体映射或受控快照。材料着色仅用 ViewObject，"
              "关闭图层时恢复。",
        "en": "Display-only preprocessing inspection. Every glyph is generated from the current "
              "project assignment and the cached CAD surface; it never writes geometry.cad.json, "
              "never changes Shape or Placement, and never enters the analysis entity map or a "
              "controlled snapshot. Material colouring uses ViewObject only and is restored when "
              "the layer is switched off."},
}

# --------------------------------------------------------------------------- #
# Coordinator-approved Chinese catalog (independent map:
# ``runtime/v0_5-language/zh_catalog_updates.json``).  Keys stay explicit and
# stable; only the Chinese display text is replaced, so every lookup source alias
# (a key, a pre-i18n Chinese literal, or a technical token) keeps working.
# ``interaction.kind.*`` / ``interaction.hover_pair`` deliberately remain the
# canonical ``Patch`` / ``Edge`` / ``Coupling`` tokens: they identify a registered
# entity, the accepted mouse tests assert them, and they are not prose.
# --------------------------------------------------------------------------- #
_ZH_OVERRIDES = {
    "app.title": "Kratos IBRA 预处理器",
    "brand.heading": "IBRA 预处理器",
    "brand.caption": "基于 B-rep 的 Kratos 结构分析",
    "brand.badge": "KRATOS 多物理场",
    "top.import_step": "+ 导入 STEP",
    "top.open_project": "打开项目",
    "top.save_project": "保存项目",
    "summary.empty": "导入 STEP 几何，在裁剪曲面上定义壳或膜模型。",
    "status.ready": "就绪 | 导入几何以定义分析模型。",
    "log.placeholder": "几何转换、模型校验与求解器日志",
    "log.show": "显示日志",
    "log.hide": "隐藏日志",
    "action.validate": "校验模型",
    "action.export": "导出分析模型  →",
    "action.stop": "停止",
    "action.new": "新建",
    "action.remove": "移除",
    "action.add_update": "添加 / 更新",
    "action.add_row": "添加行",
    "action.remove_row": "删除行",
    "action.cancel": "取消",
    "nav.geometry": "几何",
    "nav.materials": "材料",
    "nav.groups": "分组",
    "nav.boundary": "边界",
    "nav.initial": "初始",
    "nav.interfaces": "界面",
    "nav.preview": "预览",
    "nav.analysis": "分析",
    "page.geometry.title": "几何与拓扑",
    "page.materials.title": "本构属性",
    "page.groups.eyebrow": "分组",
    "page.groups.title": "命名分组与重绑定",
    "page.interfaces.title": "面片界面",
    "page.support_tab": "约束",
    "geometry.table.type": "类型",
    "geometry.table.patch": "面片 ID / 阶次",
    "geometry.open_example": "打开悬臂梁示例",
    "geometry.profile_placeholder": "可选的模型专用转换配置文件（JSON）",
    "geometry.select_profile": "选择转换配置文件",
    "geometry.refinement": "细化",
    "geometry.hover_audit": "悬浮查看",
    "geometry.hover_audit_enable": "启用悬浮查看",
    "geometry.hover_mode_all": "边 / 面片 / 耦合（全部）",
    "geometry.hover_mode_edge": "边（单侧边界）",
    "geometry.hover_mode_patch": "面片（曲面）",
    "geometry.hover_mode_coupling": "耦合（界面边）",
    "materials.remove_rows": "移除所选行",
    "materials.convert": "将数值转换为所选单位制",
    "materials.patch_assignments": "面片分配",
    "materials.patch.active": "启用",
    "materials.bulk.element": "单元列式",
    "materials.bulk.min_degree": "最低阶次",
    "materials.bulk.apply": "应用到所选面片",
    "groups.create": "从所选对象创建分组",
    "groups.remove": "移除分组",
    "groups.drop_binding": "丢弃绑定",
    "groups.rebind_or_drop": "重绑定或丢弃",
    "groups.snapshot.recover": "为快照恢复",
    "groups.snapshot.unresolved_status": "未解析",
    "groups.status_missing": "缺失 {members}",
    "conditions.assign": "分配所选实体",
    "conditions.support_method": "施加方式",
    "conditions.support_method_strong": "强施加 | 控制点",
    "conditions.support_method_penalty": "罚函数施加 | 边界 XYZ",
    "conditions.side.00": "角点 U 最小 / V 最小",
    "conditions.side.10": "角点 U 最大 / V 最小",
    "conditions.side.01": "角点 U 最小 / V 最大",
    "conditions.side.11": "角点 U 最大 / V 最大",
    "conditions.clamp": "约束相邻控制点行",
    "conditions.load_kind_vector": "全局矢量（LINE_LOAD / SURFACE_LOAD / DEAD_LOAD）",
    "conditions.load_kind_pressure": "参考法向压力（死压力）",
    "conditions.pressure": "压力（有符号标量，应力单位）",
    "conditions.amplitude": "分段线性幅值表（系数，无量纲）",
    "conditions.amplitude_time": "时间（s）",
    "conditions.amplitude_factor": "系数",
    "interfaces.confirm": "确认所选的界面",
    "interfaces.exclude": "排除所选的界面",
    "interfaces.rotation_both": "位移 + 转动",
    "preview.supports": "支承（强 / 罚函数、分量、约束）",
    "preview.loads": "载荷（XYZ 箭头、族比例/单位、零值标记）",
    "preview.frame": "背景参数坐标系（u / v / 法向）",
    "preview.materials": "材料 / 截面图例（ViewObject 颜色）",
    "preview.table.active": "启用",
    "analysis.static": "静态",
    "analysis.vtk_output": "原生 VTKHDF 结果输出",
    "analysis.output_refinement": "输出细化",
    "analysis.initialize": "初始化导出的模型",
    "analysis.run": "运行导出的分析",
    "analysis.export_geometry": "导出 CAD 几何（mm）",
    "analysis.pipeline_root": "流水线根目录",
    "analysis.time_step": "时间步长",
    "result.magnitude": "幅值",
    "result.use_last": "使用上次运行",
    "result.browse": "浏览…",
}
for _key, _chinese in _ZH_OVERRIDES.items():
    if _key in MESSAGES:
        MESSAGES[_key]["zh"] = _chinese

# These are display labels, not the canonical face/edge/coupling data tokens.
MESSAGES.update({
    "nav.analysis": {"en": "Output", "zh": "输出"},
    "result.loaded_run_status": {
        "en": "Result metadata loaded for {run}; {steps} recorded step(s).",
        "zh": "已加载 {run} 的结果元数据；共 {steps} 个时间帧。"},
    "result.drawn_status": {
        "en": "Result frame drawn: step {step} · t={time} · {component} · scale {scale}. Native sampling cells only; face/UV mapping is unavailable.",
        "zh": "已绘制结果帧：时间帧 {step} · t={time} · {component} · 变形比例 {scale}。仅显示原生采样单元；无曲面／UV 映射。"},
})
for _key, _chinese in {
    "interaction.kind.face": "曲面",
    "interaction.kind.edge": "边界边",
    "interaction.kind.coupling": "耦合边",
    "interaction.hover_pair": "曲面 {first} ↔ 曲面 {second}",
    "element.shell": "Kirchhoff–Love 壳",
    "element.membrane": "膜",
    "analysis.save_to_document": "保存到文档",
    "analysis.restore_document": "恢复文档",
    "action.add_update": "添加／更新",
    "interfaces.confirm": "确认所选接口",
    "interfaces.exclude": "排除所选接口",
    "nav.interfaces": "接口",
    "page.interfaces.eyebrow": "接口",
    "page.interfaces.title": "曲面接口",
    "preview.table.face": "曲面",
    "preview.table.orig_p": "原始次数 p (u,v)",
    "preview.table.target_p": "目标次数 p (u,v)",
    "preview.table.spans": "跨度 u/v",
    "preview.table.orig_cp": "原始控制点 u×v",
    "preview.table.est_cp": "预计控制点 u×v",
    "preview.table.insert": "插入节点 u/v",
    "preview.table.min_c": "最小连续性 C",
    "analysis.solver": "求解器",
    "analysis.analysis_type": "分析类型",
    "analysis.dynamic": "隐式动力分析",
    "analysis.linear": "线性",
    "analysis.nonlinear": "非线性",
    "analysis.start_time": "开始时间",
    "analysis.end_time": "结束时间",
    "analysis.backend_python": "后端 Python",
    "analysis.work_dir": "工作目录",
    "analysis.save_backend": "保存后端配置",
    "analysis.check_backend": "检查后端环境",
    "analysis.run_report_heading": "运行报告",
    "result.run_dir": "运行目录",
    "result.load_metadata": "加载结果元数据",
    "result.step": "时间帧",
    "result.scale": "变形比例",
    "result.component": "位移分量",
    "result.draw_frame": "绘制结果帧",
    "result.clear": "清除结果显示",
}.items():
    MESSAGES[_key]["zh"] = _chinese

# Instructions must refer to the labels actually visible in the Chinese UI.
# Only catalog literals are rewritten here; parameter/user values are never touched.
for _entry in MESSAGES.values():
    for _english, _chinese in {
        "Add / Update": "添加／更新",
        "Confirm Selected Interface": "确认所选接口",
        "Exclude Selected Interface": "排除所选接口",
        "Save to Document": "保存到文档",
        "Restore Document": "恢复文档",
        "Open JSON": "打开 JSON 项目",
        "Initialize Exported Model": "初始化导出的模型",
        "Run Exported Analysis": "运行导出的分析",
        "Refinement": "细化",
        "New 会": "新建会",
    }.items():
        _entry["zh"] = _entry["zh"].replace(_english, _chinese)
del _key, _chinese

# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
def catalog_entry(key):
    """The raw catalog entry for ``key`` or ``None``."""
    return MESSAGES.get(key)


def source_text(key, language=DEFAULT_SOURCE):
    """The stored source text of a message key in one language (default Chinese)."""
    entry = MESSAGES.get(key)
    if not isinstance(entry, dict):
        return None
    text = entry.get(normalize_language(language, default=DEFAULT_SOURCE))
    if text is None:
        text = entry.get(DEFAULT_SOURCE)
    return text


def has_language(key, language):
    entry = MESSAGES.get(key)
    return isinstance(entry, dict) and bool(entry.get(normalize_language(language, default="")))


def languages_with_text(key):
    entry = MESSAGES.get(key)
    if not isinstance(entry, dict):
        return ()
    return tuple(code for code in LANGUAGES if entry.get(code))


def _parameters(args, kwargs):
    values = {}
    if args:
        values["0"] = args[0]
        if len(args) > 1:
            values["1"] = args[1]
    values.update(kwargs)
    return values


def _format(template, values):
    if not values:
        return template
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        # A missing/extra parameter must never crash the UI or leak a traceback:
        # degrade to a plainly substituted text.
        text = template
        for name, value in values.items():
            text = text.replace("{" + str(name) + "}", str(value))
        return text


def translate(key, language=None, *args, **kwargs):
    """Translate a catalog key; unknown keys are returned unchanged.

    ``{name}`` placeholders are filled from ``kwargs`` so a translated message can
    reorder its parameters.  The catalog key can never be a user value, so a user
    name/ID/path/value is never rewritten.
    """
    if not isinstance(key, str):
        return key
    entry = MESSAGES.get(key)
    if not isinstance(entry, dict):
        return _format(key, _parameters(args, kwargs))
    code = _CURRENT if language is None else normalize_language(language)
    if key in ("msg.operation_failed", "msg.result_error") and "error" in kwargs:
        kwargs = dict(kwargs, error=localized_error(kwargs["error"], code))
    text = entry.get(code)
    if text is None:
        text = entry.get(DEFAULT_LANGUAGE)
    if text is None:
        text = entry.get(DEFAULT_SOURCE)
    if text is None:  # a catalog entry with no text at all must stay usable
        return _format(key, _parameters(args, kwargs))
    return _format(text, _parameters(args, kwargs))


def resolve_text(text, language=None):
    """Best-effort translation of a raw source string.

    Used for legacy call sites and for a lookup by stored source text: if ``text``
    is the Chinese source of exactly one catalog key, the requested language is
    returned.  A user value never matches a catalog source, so it is unchanged.
    """
    code = _CURRENT if language is None else normalize_language(language)
    if not isinstance(text, str) or not text:
        return text
    for entry in MESSAGES.values():
        if entry.get(DEFAULT_SOURCE) != text:
            continue
        replacement = entry.get(code)
        if replacement is not None and replacement != text:
            return replacement
    return text


def translate_text(text, language=None):
    """Translate a stored UI string that may be either a key or a source text."""
    if isinstance(text, str) and text in MESSAGES:
        return translate(text, language)
    return resolve_text(text, language)


def is_key(text):
    return isinstance(text, str) and text in MESSAGES


def current_language():
    return _CURRENT


def set_language(language, default=DEFAULT_LANGUAGE):
    """Set the process-wide language.  Returns the language actually applied."""
    global _CURRENT
    code = normalize_language(language, default=default)
    _CURRENT = code
    return code


# --------------------------------------------------------------------------- #
# Preference persistence (plugin's own FreeCAD parameter group only)
# --------------------------------------------------------------------------- #
def _language_file(user_data):
    """Legacy/portable fallback file: the plugin's own user-data directory."""
    if user_data is None:
        return None
    return __import__("pathlib").Path(user_data) / "IBRA" / "language.json"


def read_language_preference(user_data=None):
    """Read the stored plugin language, or ``None`` when nothing is stored.

    Resolution order: the plugin's own FreeCAD parameter preference, then its own
    user-data file.  FreeCAD's global language is **never** written and is read by
    the caller only as a first-run hint.
    """
    stored = None
    try:
        import FreeCAD as _App  # noqa: N813
        group = _App.ParamGet("User parameter:" + PREFERENCE_GROUP)
        value = group.GetString(PREFERENCE_KEY)
        stored = value or None
    except Exception:  # noqa: BLE001 - no FreeCAD/parameter store: try the file
        stored = None
    if stored:
        return normalize_language(stored, default=DEFAULT_LANGUAGE)
    path = _language_file(user_data)
    if path is not None:
        try:
            import json
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get(PREFERENCE_KEY):
                return normalize_language(data[PREFERENCE_KEY], default=DEFAULT_LANGUAGE)
        except Exception:  # noqa: BLE001 - a missing/invalid file is "not stored"
            return None
    return None


def write_language_preference(language, user_data=None):
    """Persist the plugin language in its own FreeCAD parameter preference.

    The project file, the document and FreeCAD's global language are untouched.
    Returns the normalized language.
    """
    code = normalize_language(language, default=DEFAULT_LANGUAGE)
    try:
        import FreeCAD as _App  # noqa: N813
        group = _App.ParamGet("User parameter:" + PREFERENCE_GROUP)
        group.SetString(PREFERENCE_KEY, code)
    except Exception:  # noqa: BLE001 - FreeCAD-free environments use the file
        pass
    path = _language_file(user_data)
    if path is not None:
        try:
            import json
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({PREFERENCE_KEY: code}, indent=2),
                                 encoding="utf-8")
            os.replace(temporary, path)
        except Exception:  # noqa: BLE001 - a read-only user dir is not fatal
            pass
    return code


def initialize_language(user_data=None, freecad_language=None):
    """Apply the saved language, else the first-run default.  Returns the language."""
    stored = read_language_preference(user_data)
    if stored:
        return set_language(stored)
    return set_language(default_language_from_freecad(freecad_language))


# Exact GUI-owned diagnostic phrases; unknown backend diagnostics remain raw.
_ERROR_TEXT_TRANSLATIONS = {'Several documents carry an IBRA project; open the intended document and retry.': '多个文档都载有 IBRA '
                                                                                   '项目；请打开目标文档后重试。',
 'The snapshot conversion is still running; wait for it to finish before cancelling.': '快照转换仍在进行；请等待其完成后再取消。',
 'Select patch rows or use Select All before applying batch properties.': '应用批量属性之前，请选择曲面片行或使用全选。',
 'Pressure must be finite.': '压力必须是有限值。',
 'An enabled amplitude table needs at least two time/factor points.': '已启用的幅值表至少需要两个时间/因子点。',
 'Import STEP geometry first; the current CAD is required to associate a result.': '请先导入 STEP 几何；关联结果需要当前 '
                                                                                   'CAD。',
 'The analysis document is closed. Reimport the geometry or reopen the project before drawing results.': '分析文档已关闭。绘制结果之前，请重新导入几何或重新打开项目。',
 'No time-step selector is available.': '没有可用的时间步选择器。',
 'A background task is already running.': '已有后台任务正在运行。',
 'A controlled snapshot is pending; apply or cancel it before importing other geometry so its preview and mapping are not orphaned.': '存在待处理的受控快照；导入其他几何之前请先应用或取消它，以免其预览与映射被孤立。',
 'Backend Python was not found. Configure its executable in Analysis and Output.': '未找到后端 '
                                                                                   'Python。请在“分析与输出”中配置其可执行文件。',
 'Import STEP geometry first.': '请先导入 STEP 几何。',
 'The analysis document is closed. Reimport geometry or reopen the project.': '分析文档已关闭。请重新导入几何或重新打开项目。',
 'Assign subedges or vertices of a patch individually, or select independent boundary entities in the geometry table.': '请逐个指定曲面片的子边或顶点，或在几何表中选择独立的边界实体。',
 'No FreeCAD document carries an IBRA project. Use Open JSON, or import a STEP to associate one.': '没有 '
                                                                                                   'FreeCAD '
                                                                                                   '文档载有 '
                                                                                                   'IBRA '
                                                                                                   '项目。请使用“打开 '
                                                                                                   'JSON”，或导入 '
                                                                                                   'STEP '
                                                                                                   '以关联一个项目。',
 'a native-save observer for a different document is still installed': '仍安装着针对其他文档的原生保存观察者',
 'a background task is still running': '仍有后台任务正在运行',
 'an uncommitted condition edit is open; press Add / Update or New first': '存在未提交的条件编辑；请先点击“添加/更新”或“新建”',
 'Commit or cancel the open condition edit before switching documents.': '切换文档之前，请提交或取消当前打开的条件编辑。',
 'A background task is already running; wait for it before opening a project.': '已有后台任务正在运行；请等待其完成后再打开项目。',
 'A controlled snapshot is pending; apply or cancel it before opening a project.': '存在待处理的受控快照；打开项目之前请先应用或取消它。',
 'Unsupported project schema version.': '不支持的项目 schema 版本。',
 'The STEP checksum has changed; previous boundary conditions cannot be reused.': 'STEP '
                                                                                  '校验和已变化；先前计算的边界条件无法复用。',
 'Wait for the current background task to finish.': '请等待当前后台任务完成。',
 'Commit or cancel the open condition edit before snapshotting.': '创建快照之前，请提交或取消当前打开的条件编辑。',
 'A controlled snapshot is already pending; apply or cancel it before capturing another one.': '已存在待处理的受控快照；捕获新的快照之前请先应用或取消它。',
 'No FreeCAD document is associated with this panel.': '此面板未关联任何 FreeCAD 文档。',
 'Capture a controlled snapshot first.': '请先捕获受控快照。',
 'The active document changed since this snapshot was captured; cancel it and capture again so the mapping cannot cross documents.': '自捕获此快照以来活动文档已改变；请取消它并重新捕获，以免映射跨文档。',
 'The geometry cache changed since this snapshot was captured; cancel it and capture again.': '自捕获此快照以来几何缓存已改变；请取消它并重新捕获。',
 'Convert the snapshot before applying a mapping.': '应用映射之前请先转换快照。',
 'The active document changed since this snapshot was captured; the mapping is refused because a same-looking project file is not the same document.': '自捕获此快照以来活动文档已改变；该映射被拒绝，因为外观相同的项目文件并不是同一个文档。',
 'The geometry cache changed since this snapshot was captured; the mapping is refused.': '自捕获此快照以来几何缓存已改变；该映射被拒绝。',
 'Commit or cancel the open condition edit before applying a mapping.': '应用映射之前，请提交或取消当前打开的条件编辑。',
 'The project changed after this snapshot was captured; capture it again so no edit is silently overwritten by the captured state.': '捕获此快照之后项目发生了改变；请重新捕获，以免任何编辑被所捕获的状态静默覆盖。',
 'A background task is already running; wait for it before recovering.': '已有后台任务正在运行；请等待其完成后再恢复。',
 'A controlled snapshot is pending; apply or cancel it before recovering.': '存在待处理的受控快照；恢复之前请先应用或取消它。',
 'Commit or cancel the open condition edit before recovering.': '恢复之前，请提交或取消当前打开的条件编辑。',
 "The document's conversion cache is missing or stale; a controlled snapshot needs the stored cache to preview the new geometry.": '文档的转换缓存缺失或已过期；受控快照需要已存储的缓存来预览新几何。',
 'Select analysis faces or one-sided boundaries in the document first.': '请先在文档中选择分析面或单侧边界。',
 'Groups accept faces or boundary edges only; interfaces are not group members.': '分组只接受面或边界边；接口不能作为分组成员。',
 'Select either faces or boundary edges, not a mixture.': '请选择面或边界边，而不要混合选择。',
 'Select a group row first.': '请先选择一个分组行。',
 'Select a pending entry first.': '请先选择一个待处理条目。',
 'Select the replacement entity in the document first.': '请先在文档中选择替换实体。',
 'Amplitude times must be strictly increasing; duplicate or out-of-order times are rejected.': '幅值时间必须严格递增；重复或乱序的时间会被拒绝。',
 'The deformation scale is not a number.': '变形缩放不是数值。',
 'A background task is already running; wait for it before importing.': '已有后台任务正在运行；请等待其完成后再导入。',
 'A controlled snapshot is pending; apply or cancel it before importing.': '存在待处理的受控快照；导入之前请先应用或取消它。',
 'The source STEP file has changed.': '源 STEP 文件已改变。',
 'The displayed CAD geometry has changed. Save the modified model as STEP and reimport it.': '所显示的 CAD '
                                                                                             '几何已改变。请将修改后的模型另存为 '
                                                                                             'STEP 并重新导入。',
 'Apply or discard the boundary condition you are editing (Add/Update or New) before converting units.': '转换单位之前，请应用或放弃正在编辑的边界条件（添加/更新 '
                                                                                                         '或 '
                                                                                                         '新建）。',
 'The penalty factor field overflows the target unit system.': '罚因子字段超出目标单位体系的范围。',
 'The vertex does not uniquely match a natural NURBS corner. Strong enforcement at general trimming vertices is not supported.': '该顶点无法唯一匹配自然 '
                                                                                                                                 'NURBS '
                                                                                                                                 '角点。不支持在一般裁剪顶点处进行强施加。',
 'Select an analysis patch or a one-sided boundary.': '请选择分析曲面片或单侧边界。',
 'Define interface conditions in Interfaces. For supports or loads, select a one-sided patch boundary.': '请在“接口”页定义接口条件。对于约束或载荷，请选择单侧的曲面片边界。',
 'Select a patch for initial conditions.': '请为初始条件选择一个曲面片。',
 'A condition name is required.': '必须填写条件名称。',
 'No FreeCAD document is available to associate.': '没有可关联的 FreeCAD 文档。',
 'A background task is already running; wait for it before saving.': '已有后台任务正在运行；请等待其完成后再保存。',
 'This panel is not associated with a FreeCAD document. Use Restore Document first.': '此面板未关联 FreeCAD '
                                                                                      '文档。请先使用“恢复文档”。',
 'A background task is already running; wait for it before restoring.': '已有后台任务正在运行；请等待其完成后再恢复。',
 'A controlled snapshot is pending; apply or cancel it before restoring.': '存在待处理的受控快照；恢复之前请先应用或取消它。',
 'Commit or cancel the open condition edit before restoring.': '恢复之前，请提交或取消当前打开的条件编辑。',
 'The document stores no source STEP; reimport the geometry first.': '该文档未存储源 STEP；请先重新导入几何。',
 'A controlled snapshot is pending; apply or cancel it before opening another project.': '存在待处理的受控快照；打开另一个项目之前请先应用或取消它。',
 'Export a complete analysis model first.': '请先导出完整的分析模型。',
 'A group rebinds to faces or boundary edges of one kind.': '一个分组只能重绑定到同一类别的面或边界边。',
 'Rebind a condition to exactly one target entity.': '请将条件重绑定到恰好一个目标实体。',
 'Select a real time-step index from the loaded metadata.': '请从已加载的元数据中选择一个真实的时间步索引。',
 'The selected time-step index is not an integer.': '所选的时间步索引不是整数。',
 'A background task is already running; wait for it before loading results.': '已有后台任务正在运行；请等待其完成后再加载结果。',
 'Select an isolated run directory (or use the last run) first.': '请先选择一个隔离的运行目录（或使用上次运行）。',
 'A background task is already running; wait for it before drawing results.': '已有后台任务正在运行；请等待其完成后再绘制结果。',
 'Load the result metadata for a run directory before drawing a frame.': '绘制帧之前，请先加载运行目录的结果元数据。',
 'Named groups are not supported for initial fields.': '初始场不支持命名分组。',
 'Assign selected entities or choose a named group first.': '请先指定所选实体或选择一个命名分组。',
 'Assign one target entity when editing a single condition.': '编辑单个条件时，请指定一个目标实体。',
 'Reference pressure applies to the whole face, not a corner or natural side.': '参考法向压力作用于整个面，而不是角点或自然边。',
 'Reference pressure requires a face group or a face target; edge targets are not supported.': '参考法向压力需要面分组或面目标；不支持边目标。',
 'Reference pressure requires a face target; select a patch face or a face group, not an edge.': '参考法向压力需要面目标；请选择曲面片面或面分组，而不是边。'}

def localized_error(value, language=None):
    text = str(value)
    code = current_language() if language is None else normalize_language(language)
    return _ERROR_TEXT_TRANSLATIONS.get(text, text) if code == "zh" else text
