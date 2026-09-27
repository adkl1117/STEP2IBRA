"""V0.5 mouse selection and coupling inspection.

Split in two halves that are deliberately independent:

* **Pure core** (refusals, :class:`Target`/:class:`RawSelection`, the
  :class:`EntityResolver`, the :class:`SelectionMachine` and the reversible
  :class:`VisibilityController`) imports neither FreeCAD nor Qt, so it runs in the
  backend venv and is unit-tested without a GUI.
* **Native bridge** (:class:`SelectionObserver`, :class:`CoinEventBridge`,
  :class:`DocumentCloseObserver` and :class:`InteractionCoordinator`) receives the
  real ``FreeCADGui``/``FreeCAD`` modules at call time and registers/removes the
  actual Selection observer and Coin ``SoEvent`` callback.  Nothing here changes the
  analysis domain, the project decisions or the exported case: the only persistent
  writes are the existing ``capture``/``coupling_decision`` entry points the panel
  already exposes, and every temporary view change is saved and restored.

The machine is *coherent*: a refused target never mutates any state, middle-confirm
only accepts the entity the left-selection actually confirmed, and the coupling flow
can only ever resolve an interface that already exists as a candidate record - a
shared geometric edge never invents one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import i18n

# --------------------------------------------------------------------------- #
# Vocabulary and next-step guidance (Chinese source text, selectable language)
# --------------------------------------------------------------------------- #
# Every guidance/refusal literal below is the **Chinese source text** of the
# catalog key with the same suffix and is byte-identical to the pre-i18n string.
# ``SelectionMachine.language`` selects the displayed language; the default is
# Chinese so the accepted pure-core behavior and its tests are unchanged.
KIND_PATCH = "face"
KIND_EDGE = "edge"
KIND_COUPLING = "coupling"
KINDS = (KIND_PATCH, KIND_EDGE, KIND_COUPLING)

HOVER_MODES = ("all", "edge", "patch", "coupling")
HOVER_MODE_KIND = {"all": KINDS, "edge": (KIND_EDGE,),
                   "patch": (KIND_PATCH,), "coupling": (KIND_COUPLING,)}

BOUNDARY_CATEGORIES = ("supports", "loads", "initial_conditions")
#: Canonical category -> Chinese source label (kept for compatibility).
CATEGORY_LABELS = {"supports": "支承", "loads": "载荷", "initial_conditions": "初始场"}


def category_key(category: str) -> str:
    """The catalog key of a boundary category label (never a user value)."""
    return "interaction.category." + str(category)


def category_label(category: str, language=None) -> str:
    return i18n.translate(category_key(category), language)


GUIDANCE_IDLE = ("鼠标点选：在边界／初始页点击“启动物理点选”，左键选择几何，"
                 "中键确认，Esc 取消；悬浮可查看已注册实体编号。")
GUIDANCE_NEED_START = "尚未启动点选：请先点击“启动物理点选”或“启动接口点选流程”。"
GUIDANCE_STARTED = ("{label}点选已启动：左键点选目标（{allowed}），中键确认当前选择，Esc 取消。")
GUIDANCE_STARTED_COUPLING = "接口点选已启动：左键点选第一个候选耦合面，Esc 取消。"
GUIDANCE_SELECTED = "已选：{items}。可继续左键点选或中键确认。"
GUIDANCE_CONFIRMED = ("目标已确认：请在右侧 {label} 表单填写参数，然后点击 Add / Update 提交；"
                      "中键只在点选过程中被占用，正常视图导航不受影响。")
GUIDANCE_COUPLING_FACE2 = "左键点选另一个候选耦合面（必须与第一个面存在现有候选接口）。"
GUIDANCE_COUPLING_EDGE = "左键点选对应的候选耦合边（Coupling {edge}）。"
GUIDANCE_COUPLING_EDGE_MULTI = ("两个面之间存在多条候选接口（Coupling {edges}）：请左键点选你们"
                                "要使用的那一条。")
GUIDANCE_COUPLING_EDGE_FIRST = "必须先用左键点选该候选耦合边（Coupling {edge}），再用中键确认。"
GUIDANCE_COUPLING_MIDDLE = "中键在耦合边 Coupling {edge} 上再次点选以确认目标。"
GUIDANCE_COUPLING_DECIDED = ("接口 Coupling {edge} 目标已确认；右侧已显示连接类型与罚因子。"
                             "请点击 Confirm Selected Interface 或 Exclude Selected Interface "
                             "显式保存连接意图。")
GUIDANCE_CANCELLED = "已取消点选，临时回调和临时显示已清除。"
GUIDANCE_PAGE_SWITCH = "已切换页面：临时回调和临时显示已清除；回到页面可重新启动点选。"
GUIDANCE_IMPORT = "几何已重新导入：临时点选状态和临时显示已清除。"
GUIDANCE_DOC_CLOSED = "文档已关闭：临时回调和临时显示已清除。"
GUIDANCE_ISOLATED = "已只显示耦合边（{count} 条）；再次点击可恢复先前的可见性。"
GUIDANCE_RESTORED = "已恢复先前可见性；分析域与接口决策未改变。"
GUIDANCE_COMMITTING = "正在把中键确认的目标写入编辑器…"
GUIDANCE_CAPTURE_FAILED = ("目标未被写入编辑器：捕获被拒绝或结果不匹配，未确认任何条件；"
                           "请检查右侧提示后重新点选。")
GUIDANCE_REVEALED = "已在点选期间临时显示原隐藏的接口 {edge}；取消或切换选择后恢复。"


class InteractionRefusal(ValueError):
    """A rejected mouse-selection request carrying its Chinese guidance text.

    ``key``/``params`` (optional) let the same refusal be re-rendered in another
    language without re-resolving anything: only the presentation changes.
    """

    def __init__(self, code: str, message: str, key: Optional[str] = None,
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.key = key
        self.params = dict(params) if params else {}

    def message_for(self, language=None) -> str:
        if self.key:
            return i18n.translate(self.key, language, **self.params)
        return self.message


# --------------------------------------------------------------------------- #
# Pure data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Target:
    """One resolved, registered analysis entity."""

    kind: str
    ident: int
    object: str
    side: Optional[str] = None
    sub_names: Tuple[str, ...] = ()

    def key(self) -> Tuple:
        return (self.kind, int(self.ident), self.side)

    def record(self) -> Dict[str, Any]:
        record = {"target_kind": self.kind, "target_id": int(self.ident)}
        if self.side:
            record["side"] = self.side
        return record

    def audit_key(self) -> str:
        """The kind is a canonical token; the visible label is a catalog key."""
        return "interaction.kind." + str(self.kind)

    def audit_label(self, language=None) -> str:
        name = i18n.translate(self.audit_key(), language)
        label = f"{name} {int(self.ident)}"
        if self.side:
            label += i18n.translate("interaction.corner", language, side=self.side)
        return label


@dataclass(frozen=True)
class RawSelection:
    """What the native layer knows about one picked object before validation."""

    document: str
    object: str
    kind: Optional[str] = None
    ident: Optional[int] = None
    sub_names: Tuple[str, ...] = ()
    fingerprint: Optional[Tuple] = None


@dataclass
class Outcome:
    """The machine's answer: guidance text plus panel events. No side effects.

    ``guidance_key``/``guidance_params`` and ``notice_key``/``notice_params`` are
    the language-independent sources of the rendered strings, so the panel can
    re-render exactly the same step after a language switch.
    """

    guidance: str = ""
    notice: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    target: Optional[Target] = None
    refused: Optional[str] = None
    guidance_key: Optional[str] = None
    guidance_params: Dict[str, Any] = field(default_factory=dict)
    notice_key: Optional[str] = None
    notice_params: Dict[str, Any] = field(default_factory=dict)
    parts: List[Dict[str, Any]] = field(default_factory=list)
    tone: str = "normal"

    @property
    def ok(self) -> bool:
        return self.refused is None


def refusal_outcome(error: InteractionRefusal, language=None) -> Outcome:
    return Outcome(notice=error.message_for(language), refused=error.code,
                   notice_key=error.key, notice_params=dict(error.params))


def hover_mode_allows(mode: str, kind: str) -> bool:
    return kind in HOVER_MODE_KIND.get(mode, KINDS)


def format_hover(target: Optional[Target], coupling_faces: Optional[Sequence[int]] = None,
                 language=None) -> Optional[str]:
    """Auditable hover text: registered kind + ID, plus the two interface sides."""
    if target is None:
        return None
    text = target.audit_label(language)
    if target.kind == KIND_COUPLING and coupling_faces:
        faces = [int(face) for face in coupling_faces]
        if len(faces) == 2:
            text += i18n.translate("interaction.hover_pair", language,
                                   first=faces[0], second=faces[1])
    return text


# --------------------------------------------------------------------------- #
# Pure resolution (foreign document / preview / stale geometry / sub-edges)
# --------------------------------------------------------------------------- #
SubedgeMatcher = Callable[[RawSelection, Target], Optional[Target]]


class EntityResolver:
    """Turns a :class:`RawSelection` into a registered :class:`Target` or refuses.

    Every refusal is atomic: the caller gets an exception and nothing was changed.
    """

    def __init__(self, *, document: Optional[str], entities: Dict[Tuple, str],
                 fingerprints: Optional[Dict[str, Tuple]] = None,
                 preview_objects: Iterable[str] = (), result_objects: Iterable[str] = (),
                 subedge_matcher: Optional[SubedgeMatcher] = None,
                 language=None):
        self.document = document
        self.entities = dict(entities or {})
        self.by_name = {name: key for key, name in self.entities.items() if name}
        self.fingerprints = dict(fingerprints or {})
        self.preview_objects = {str(name) for name in preview_objects if name}
        self.result_objects = {str(name) for name in result_objects if name}
        self.subedge_matcher = subedge_matcher
        self.language = i18n.normalize_language(language, default=i18n.DEFAULT_SOURCE)

    def _refuse(self, code: str, key: str, **params) -> "InteractionRefusal":
        return InteractionRefusal(code, i18n.translate(key, self.language, **params),
                                  key=key, params=params)

    def resolve(self, raw: RawSelection) -> Target:
        if not self.document or not raw.document or raw.document != self.document:
            raise self._refuse("foreign_document", "interaction.refusal.foreign_document")
        if raw.object in self.preview_objects or raw.object in self.result_objects:
            raise self._refuse("preview_or_result", "interaction.refusal.preview_or_result")
        key = self.by_name.get(raw.object)
        if key is None:
            raise self._refuse("unregistered", "interaction.refusal.unregistered")
        kind, ident = key
        ident = int(ident)
        if kind not in KINDS:
            raise self._refuse("bad_kind", "interaction.refusal.bad_kind", kind=repr(kind))
        if raw.kind is not None and (raw.kind != kind or (raw.ident is not None and int(raw.ident) != ident)):
            raise self._refuse("inconsistent_tag", "interaction.refusal.inconsistent_tag")
        recorded = self.fingerprints.get(raw.object)
        if recorded is None:
            raise self._refuse("stale_geometry", "interaction.refusal.no_fingerprint")
        if raw.fingerprint is None or tuple(raw.fingerprint) != tuple(recorded):
            raise self._refuse("stale_geometry", "interaction.refusal.geometry_changed")

        base = Target(kind=kind, ident=ident, object=raw.object)
        if not raw.sub_names:
            return base
        if len(raw.sub_names) > 1:
            raise self._refuse("subelement", "interaction.refusal.one_subelement")
        if kind != KIND_PATCH:
            # A registered one-sided boundary or interface edge is a single entity.
            # ``getObjectInfo`` reports its own Component (e.g. ``Edge1``); that is the
            # same registered entity, so the whole target is returned instead of being
            # refused as a foreign sub-edge.  Patch sub-edges go through the matcher.
            return base
        if self.subedge_matcher is None:
            raise self._refuse("ambiguous_subedge", "interaction.refusal.ambiguous_subedge")
        resolved = self.subedge_matcher(raw, base)
        if resolved is None:
            raise self._refuse("ambiguous_subedge", "interaction.refusal.ambiguous_subedge")
        return resolved


# --------------------------------------------------------------------------- #
# Pure state machine
# --------------------------------------------------------------------------- #
class SelectionMachine:
    """Mode/step bookkeeping for the boundary and coupling mouse flows.

    ``language`` selects the guidance language (default Chinese, the historical
    behavior).  Every state transition stores the message *key* and its parameters,
    so :meth:`set_language` re-renders the current step without changing any
    selection state.
    """

    def __init__(self, language=None):
        self.mode = "idle"                 # idle | boundary | coupling
        self.category: Optional[str] = None
        self.pressure = False
        self.step: Optional[str] = None    # face1 | face2 | edge
        self.targets: List[Target] = []
        self.face1: Optional[Target] = None
        self.face2: Optional[Target] = None
        self.candidate_edge: Optional[int] = None
        self.candidate_edges: List[int] = []
        self.edge_picked = False
        self.confirmed_edge: Optional[int] = None
        self.couplings: List[Dict[str, Any]] = []
        self.hover_mode = "all"
        self.language = i18n.normalize_language(language, default=i18n.DEFAULT_SOURCE)
        self._guidance_key: Optional[str] = "interaction.guidance.idle"
        self._guidance_args: Dict[str, Any] = {}
        self._raw_guidance: Optional[str] = None
        self._notice_key: Optional[str] = None
        self._notice_args: Dict[str, Any] = {}
        self._raw_notice: Optional[str] = None
        self.hover_text: Optional[str] = None
        self.hover_parts: List[Dict[str, Any]] = []
        self.last_refusal: Optional[str] = None
        # ``guidance``/``notice`` are always the currently rendered text.
        self.guidance = i18n.translate("interaction.guidance.idle", self.language)
        self.notice: Optional[str] = None

    # -- rendered text -----------------------------------------------------
    # A message parameter can itself be another catalog key (a category label, an
    # allowed-targets phrase) or the live target list.  Those are stored as typed
    # markers and resolved **at render time**, so switching language can never mix
    # an old-language nested label into a new-language template.
    def label_marker(self, key: str, **params) -> Dict[str, Any]:
        return {"__i18n_key__": str(key), "params": dict(params)}

    def targets_marker(self) -> Dict[str, Any]:
        return {"__i18n_targets__": True}

    def _resolve_param(self, value, language):
        if isinstance(value, dict):
            if "__i18n_key__" in value:
                inner = {key: self._resolve_param(item, language)
                         for key, item in (value.get("params") or {}).items()}
                return i18n.translate(value["__i18n_key__"], language, **inner)
            if value.get("__i18n_targets__"):
                if not self.targets:
                    return i18n.translate("interaction.targets.empty", language)
                return "、".join(entry.audit_label(language) for entry in self.targets)
        return value

    def render_message(self, key: str, params=None, language=None) -> str:
        code = self.language if language is None else language
        resolved = {name: self._resolve_param(value, code)
                    for name, value in (params or {}).items()}
        return i18n.translate(key, code, **resolved)

    def set_message(self, key: str, **params):
        """Select the current guidance message by catalog key."""
        self._guidance_key = key
        self._guidance_args = dict(params)
        self._raw_guidance = None
        self.guidance = self.render_message(key, self._guidance_args)
        return self.guidance

    def set_notice(self, key: Optional[str], **params):
        self._notice_key = key
        self._notice_args = dict(params)
        if key is None:
            self._raw_notice = None
            self.notice = None
        else:
            self._raw_notice = None
            self.notice = self.render_message(key, self._notice_args)
        return self.notice

    def adopt_notice(self, outcome: "Outcome"):
        """Take a notice/refusal from an outcome, keeping its renderable source."""
        if outcome.notice is None:
            if outcome.ok and outcome.refused is None:
                return
            return
        if outcome.notice_key:
            self._notice_key = outcome.notice_key
            self._notice_args = dict(outcome.notice_params)
            self._raw_notice = None
            self.notice = self.render_message(outcome.notice_key, self._notice_args)
        else:
            self._notice_key = None
            self._notice_args = {}
            self._raw_notice = outcome.notice
            self.notice = outcome.notice

    def set_hover_parts(self, parts: Sequence[Dict[str, Any]]):
        """Compose the hover audit line from typed parts (never a user value)."""
        self.hover_parts = [dict(part) for part in parts]
        self.hover_text = self._render_parts(self.hover_parts) or None
        return self.hover_text

    def _render_parts(self, parts) -> str:
        chunks = []
        for part in parts:
            kind = part.get("part")
            if kind == "text":
                chunks.append(str(part.get("value", "")))
            elif kind == "label":
                chunks.append(i18n.translate(part["key"], self.language,
                                             **(part.get("params") or {})))
            elif kind == "ref":
                chunks.append(f"{i18n.translate(part['key'], self.language)} {part.get('id')}")
            elif kind == "ref_pair":
                # The two interface sides are one audit fact, not two separate ones.
                chunks.append(i18n.translate("interaction.hover_pair", self.language,
                                             first=part.get("first"), second=part.get("second")))
        return " · ".join(chunk for chunk in chunks if chunk)

    def set_language(self, language):
        """Re-render every current string in the new language; state is untouched."""
        code = i18n.normalize_language(language, default=self.language)
        self.language = code
        if self._guidance_key is not None:
            self.guidance = self.render_message(self._guidance_key, self._guidance_args)
        elif self._raw_guidance is not None:
            self.guidance = self._raw_guidance
        if self._notice_key is not None:
            self.notice = self.render_message(self._notice_key, self._notice_args)
        elif self._raw_notice is not None:
            self.notice = self._raw_notice
        if self.hover_parts:
            self.hover_text = self._render_parts(self.hover_parts) or None
        return code

    @property
    def language_settings(self) -> Dict[str, Any]:
        """Diagnostics only: the stored (language-independent) message sources."""
        return {"guidance_key": self._guidance_key, "guidance_args": dict(self._guidance_args),
                "notice_key": self._notice_key, "notice_args": dict(self._notice_args),
                "raw_guidance": self._raw_guidance, "raw_notice": self._raw_notice,
                "hover_parts": [dict(part) for part in self.hover_parts]}

    # -- rendering ---------------------------------------------------------
    def render_guidance(self) -> str:
        lines = [self.guidance or i18n.translate("interaction.guidance.idle", self.language)]
        if self.notice:
            lines.append(i18n.translate("interaction.notice_prefix", self.language,
                                        text=self.notice))
        if self.hover_text:
            lines.append(i18n.translate("interaction.hover_prefix", self.language,
                                        text=self.hover_text))
        return "\n".join(lines)

    def active(self) -> bool:
        return self.mode != "idle"

    # -- configuration -----------------------------------------------------
    def set_hover_mode(self, mode: str) -> Outcome:
        if mode not in HOVER_MODES:
            return refusal_outcome(InteractionRefusal(
                "hover_mode",
                i18n.translate("interaction.refusal.hover_mode", self.language,
                               mode=repr(mode), modes=", ".join(HOVER_MODES)),
                key="interaction.refusal.hover_mode",
                params={"mode": repr(mode), "modes": ", ".join(HOVER_MODES)}))
        self.hover_mode = mode
        return Outcome(guidance=self.guidance, guidance_key=self._guidance_key,
                       guidance_params=dict(self._guidance_args))

    def set_couplings(self, records: Sequence[Dict[str, Any]]):
        self.couplings = [dict(record) for record in records or []]

    def reset(self):
        self.mode = "idle"
        self.category = None
        self.pressure = False
        self.step = None
        self.targets = []
        self.face1 = self.face2 = None
        self.candidate_edge = None
        self.candidate_edges = []
        self.edge_picked = False
        self.hover_text = None
        self.hover_parts = []
        self.last_refusal = None

    # -- starts ------------------------------------------------------------
    def start_boundary(self, category: str, *, pressure: bool = False) -> Outcome:
        if category not in BOUNDARY_CATEGORIES:
            return refusal_outcome(InteractionRefusal(
                "category",
                i18n.translate("interaction.refusal.category", self.language,
                               category=repr(category)),
                key="interaction.refusal.category", params={"category": repr(category)}))
        self.reset()
        self.mode = "boundary"
        self.category = category
        self.pressure = bool(pressure)
        self.set_message("interaction.guidance.started",
                         label=self.label_marker(category_key(category)),
                         allowed=self.label_marker(self._allowed_key()))
        return Outcome(guidance=self.guidance, guidance_key=self._guidance_key,
                       guidance_params=dict(self._guidance_args),
                       parts=self._guidance_parts(), tone="active")

    def start_coupling(self) -> Outcome:
        self.reset()
        self.mode = "coupling"
        self.step = "face1"
        self.set_message("interaction.guidance.started_coupling")
        return Outcome(guidance=self.guidance, guidance_key=self._guidance_key,
                       guidance_params=dict(self._guidance_args),
                       parts=self._guidance_parts(), tone="active")

    def set_pressure(self, pressure: bool):
        self.pressure = bool(pressure)

    # -- hover -------------------------------------------------------------
    def hover(self, target: Optional[Target]) -> Optional[str]:
        if target is None:
            self.hover_parts = []
            self.hover_text = None
            return None
        parts: List[Dict[str, Any]] = [{"part": "ref", "key": target.audit_key(),
                                        "id": int(target.ident)}]
        if target.side:
            parts.append({"part": "label", "key": "interaction.hover.corner",
                          "params": {"side": target.side}})
        if target.kind == KIND_COUPLING:
            faces = self._faces_of(target.ident)
            if faces and len(faces) == 2:
                parts.append({"part": "ref_pair", "first": faces[0], "second": faces[1]})
        self.hover_parts = parts
        text = self._render_parts(parts)
        if not hover_mode_allows(self.hover_mode, target.kind):
            text += i18n.translate("interaction.hover.hidden_kind", self.language)
        self.hover_text = text
        return text

    # -- left / middle / escape -------------------------------------------
    def left(self, target: Target, *, additive: bool = False) -> Outcome:
        if self.mode == "idle":
            return self._need_start()
        if self.mode == "boundary":
            return self._left_boundary(target, additive=additive)
        return self._left_coupling(target)

    def _need_start(self) -> Outcome:
        return refusal_outcome(InteractionRefusal(
            "not_started", GUIDANCE_NEED_START,
            key="interaction.guidance.need_start"), self.language)

    def _left_boundary(self, target: Target, *, additive: bool) -> Outcome:
        accepted, reason_key, reason = self._accepts(target)
        if not accepted:
            return refusal_outcome(InteractionRefusal(
                "bad_target", reason, key=reason_key), self.language)
        if additive:
            if target.key() in [entry.key() for entry in self.targets]:
                self.targets = [entry for entry in self.targets if entry.key() != target.key()]
            else:
                self.targets = self.targets + [target]
        else:
            self.targets = [target]
        self.set_notice(None)
        self.set_message("interaction.guidance.selected", items=self.targets_marker())
        return Outcome(guidance=self.guidance, target=target,
                       guidance_key=self._guidance_key,
                       guidance_params=dict(self._guidance_args),
                       parts=self._guidance_parts(), tone="selected",
                       events=[{"kind": "highlight_targets",
                                "targets": [entry.record() for entry in self.targets],
                                "objects": [{"object": entry.object,
                                             "sub_names": list(entry.sub_names)}
                                            for entry in self.targets]}])

    def _targets_text(self) -> str:
        if not self.targets:
            return "（空）"
        return "、".join(entry.audit_label(self.language) for entry in self.targets)

    def _left_coupling(self, target: Target) -> Outcome:
        if self.step == "face1":
            if target.kind != KIND_PATCH:
                return refusal_outcome(InteractionRefusal(
                    "coupling_flow_face", "接口流程要求先左键点选候选曲面片。",
                    key="interaction.refusal.coupling_flow_face"), self.language)
            self.face1 = target
            self.step = "face2"
            self.set_notice(None)
            self.set_message("interaction.guidance.coupling_face2")
            return Outcome(
                guidance=self.guidance, target=target,
                guidance_key=self._guidance_key, guidance_params=dict(self._guidance_args),
                parts=self._guidance_parts(), tone="selected",
                events=[{"kind": "highlight_coupling_candidates",
                         "face_id": target.ident,
                         "edge_ids": self._candidates_for_face(target.ident)}])
        if self.step == "face2":
            if target.kind != KIND_PATCH:
                return refusal_outcome(InteractionRefusal(
                    "coupling_flow_face", "接口流程要求先左键点选候选曲面片。",
                    key="interaction.refusal.coupling_flow_face"), self.language)
            if self.face1 is not None and target.ident == self.face1.ident:
                return refusal_outcome(InteractionRefusal(
                    "coupling_same_face", "第二个面必须与第一个面不同；状态未改动。",
                    key="interaction.refusal.coupling_same_face"), self.language)
            edges = self._candidates_between(self.face1.ident, target.ident)
            if not edges:
                return refusal_outcome(InteractionRefusal(
                    "no_existing_candidate",
                    "这两个面之间没有现有候选接口；不会因相邻而虚构接口，状态未改动。",
                    key="interaction.refusal.no_existing_candidate"), self.language)
            self.face2 = target
            self.candidate_edges = edges
            self.candidate_edge = edges[0] if len(edges) == 1 else None
            self.edge_picked = False
            self.step = "edge"
            self.set_notice(None)
            if len(edges) == 1:
                self.set_message("interaction.guidance.coupling_edge", edge=edges[0])
                event = {"kind": "highlight_coupling", "edge_id": int(edges[0])}
            else:
                joined = ", ".join(str(edge) for edge in edges)
                self.set_message("interaction.guidance.coupling_edge_multi", edges=joined)
                event = {"kind": "highlight_coupling_candidates",
                         "face_id": target.ident, "edge_ids": edges}
            return Outcome(guidance=self.guidance, target=target,
                           guidance_key=self._guidance_key,
                           guidance_params=dict(self._guidance_args),
                           parts=self._guidance_parts(), tone="selected", events=[event])
        if self.step == "edge":
            if target.kind != KIND_COUPLING:
                return refusal_outcome(InteractionRefusal(
                    "coupling_edge_required",
                    "请点选接口边（Coupling），而不是普通边界；状态未改动。",
                    key="interaction.refusal.coupling_edge_required"), self.language)
            if target.ident not in self.candidate_edges:
                return refusal_outcome(InteractionRefusal(
                    "wrong_coupling_edge",
                    "点选的耦合边与两个面之间的现有候选不一致；状态未改动。",
                    key="interaction.refusal.wrong_coupling_edge"), self.language)
            # Only an explicit LEFT edge pick arms the middle confirm.
            self.candidate_edge = int(target.ident)
            self.edge_picked = True
            self.set_notice(None)
            self.set_message("interaction.guidance.coupling_middle", edge=self.candidate_edge)
            return Outcome(guidance=self.guidance, target=target,
                           guidance_key=self._guidance_key,
                           guidance_params=dict(self._guidance_args),
                           parts=self._guidance_parts(), tone="selected",
                           events=[{"kind": "highlight_coupling", "edge_id": self.candidate_edge}])
        return refusal_outcome(InteractionRefusal(
            "coupling_state", "接口点选状态异常，请按 Esc 取消后重新启动。",
            key="interaction.refusal.coupling_state"), self.language)

    def middle(self, target: Target) -> Outcome:
        if self.mode == "boundary":
            return self._middle_boundary(target)
        if self.mode == "coupling":
            return self._middle_coupling(target)
        return self._need_start()

    def _middle_boundary(self, target: Target) -> Outcome:
        if not self.targets:
            return refusal_outcome(InteractionRefusal(
                "no_target", "请先用左键点选目标，再用中键确认。",
                key="interaction.refusal.no_target"), self.language)
        if target.key() not in [entry.key() for entry in self.targets]:
            return refusal_outcome(InteractionRefusal(
                "middle_mismatch", "中键确认必须与当前左键所选目标一致；状态未改动。",
                key="interaction.refusal.middle_mismatch"), self.language)
        category = self.category
        records = [entry.record() for entry in self.targets]
        objects = [{"object": entry.object, "sub_names": list(entry.sub_names)}
                   for entry in self.targets]
        self.targets = []
        self.mode = "idle"
        self.step = None
        self.hover_text = None
        self.hover_parts = []
        self.set_notice(None)
        # The confirmed text is only shown after the coordinator has verified that the
        # real capture actually wrote these targets; a guarded capture failure must not
        # leave a false confirmation on screen.
        self.set_message("interaction.guidance.committing")
        confirmed_key = ("interaction.guidance.confirmed_pressure"
                         if self.pressure and category == "loads"
                         else "interaction.guidance.confirmed")
        return Outcome(guidance=self.guidance, target=target,
                       guidance_key=self._guidance_key,
                       guidance_params=dict(self._guidance_args),
                       parts=self._guidance_parts(), tone="committing",
                       events=[{"kind": "capture", "category": category,
                                "targets": records, "objects": objects,
                                "guidance_key": confirmed_key,
                                "guidance_params": {"label": self.label_marker(category_key(category))},
                                # Legacy key kept for the accepted pre-i18n probes:
                                # always the Chinese source rendering.
                                "guidance_after": i18n.translate(
                                    confirmed_key, i18n.DEFAULT_SOURCE,
                                    label=category_label(category, i18n.DEFAULT_SOURCE))}])

    def _middle_coupling(self, target: Target) -> Outcome:
        if self.step != "edge" or self.candidate_edge is None:
            return refusal_outcome(InteractionRefusal(
                "coupling_confirm_order",
                "请先依次左键选择两个候选面和对应耦合边，然后中键确认。",
                key="interaction.refusal.coupling_confirm_order"), self.language)
        if not self.edge_picked:
            # A middle click alone must never skip the explicit LEFT edge pick.
            return refusal_outcome(InteractionRefusal(
                "coupling_edge_first",
                GUIDANCE_COUPLING_EDGE_FIRST.format(edge=self.candidate_edge),
                key="interaction.guidance.coupling_edge_first",
                params={"edge": self.candidate_edge}), self.language)
        if target.kind != KIND_COUPLING or target.ident != self.candidate_edge:
            return refusal_outcome(InteractionRefusal(
                "middle_mismatch", "中键确认必须在同一条候选耦合边上；状态未改动。",
                key="interaction.refusal.middle_mismatch_coupling"), self.language)
        edge = int(self.candidate_edge)
        record = self._record(edge) or {}
        self.confirmed_edge = edge
        self.mode = "idle"
        self.step = None
        self.face1 = self.face2 = None
        self.candidate_edge = None
        self.candidate_edges = []
        self.edge_picked = False
        self.hover_text = None
        self.hover_parts = []
        self.set_notice(None)
        self.set_message("interaction.guidance.coupling_decided", edge=edge)
        return Outcome(
            guidance=self.guidance, target=target,
            guidance_key=self._guidance_key, guidance_params=dict(self._guidance_args),
            parts=self._guidance_parts(), tone="confirmed",
            events=[{"kind": "select_coupling_row", "edge_id": edge},
                    {"kind": "expose_coupling_decision", "edge_id": edge,
                     "penalty": record.get("penalty"),
                     "rotation": bool(record.get("rotation")),
                     "state": record.get("state")}])

    def escape(self) -> Outcome:
        self.reset()
        self.set_message("interaction.guidance.cancelled")
        return Outcome(guidance=self.guidance, guidance_key=self._guidance_key,
                       guidance_params=dict(self._guidance_args),
                       parts=self._guidance_parts(), tone="cancelled")

    # -- helpers -----------------------------------------------------------
    def current_key(self):
        """The stored, language-independent source of the current guidance line."""
        return self._guidance_key, dict(self._guidance_args)

    def current_outcome(self) -> Outcome:
        """A fresh outcome carrying the current guidance source (for a re-render)."""
        return Outcome(guidance=self.guidance,
                       guidance_key=self._guidance_key,
                       guidance_params=dict(self._guidance_args),
                       parts=self._guidance_parts(), tone="current")

    def _guidance_parts(self):
        return [{"part": "guidance", "key": self._guidance_key,
                 "params": dict(self._guidance_args)}] if self._guidance_key else []

    def _allowed_key(self) -> str:
        if self.category == "initial_conditions":
            return "interaction.allowed.initial"
        if self.category == "loads" and self.pressure:
            return "interaction.allowed.pressure"
        if self.category == "loads":
            return "interaction.allowed.loads"
        return "interaction.allowed.supports"

    def _allowed_text(self) -> str:
        return i18n.translate(self._allowed_key(), self.language)

    def _accepts(self, target: Target) -> Tuple[bool, Optional[str], str]:
        if target.kind == KIND_COUPLING:
            key = "interaction.refusal.coupling_as_boundary"
            return False, key, i18n.translate(key, self.language)
        if target.kind not in (KIND_PATCH, KIND_EDGE):
            key = "interaction.refusal.unsupported_kind"
            return False, key, i18n.translate(key, self.language,
                                              kind=repr(target.kind))
        whole_face_only = (self.category == "initial_conditions"
                           or (self.category == "loads" and self.pressure))
        if whole_face_only and target.kind != KIND_PATCH:
            key = ("interaction.refusal.initial_face_only"
                   if self.category == "initial_conditions"
                   else "interaction.refusal.pressure_face_only")
            return False, key, i18n.translate(key, self.language)
        if whole_face_only and target.side:
            key = ("interaction.refusal.initial_whole_face"
                   if self.category == "initial_conditions"
                   else "interaction.refusal.pressure_whole_face")
            return False, key, i18n.translate(key, self.language)
        return True, None, ""

    def _record(self, edge_id: int) -> Optional[Dict[str, Any]]:
        return next((record for record in self.couplings
                     if int(record.get("edge_id", -1)) == int(edge_id)), None)

    def _faces_of(self, edge_id: int) -> Optional[List[int]]:
        record = self._record(edge_id)
        if not record:
            return None
        faces = record.get("face_ids") or record.get("faces")
        if isinstance(faces, (list, tuple)) and len(faces) == 2:
            return [int(faces[0]), int(faces[1])]
        return None

    def _candidates_for_face(self, face_id: int) -> List[int]:
        found = []
        for record in self.couplings:
            faces = record.get("face_ids") or record.get("faces") or []
            if face_id in [int(face) for face in faces] and record.get("edge_id") is not None:
                found.append(int(record["edge_id"]))
        return sorted(set(found))

    def _candidates_between(self, first: int, second: int) -> List[int]:
        """Every existing candidate interface joining the two faces (may be >1)."""
        matches = []
        for record in self.couplings:
            faces = record.get("face_ids") or record.get("faces") or []
            if {int(face) for face in faces} == {int(first), int(second)} and len(faces) == 2:
                matches.append(int(record["edge_id"]))
        return sorted(set(matches))


# --------------------------------------------------------------------------- #
# Reversible coupling-only visibility
# --------------------------------------------------------------------------- #
class VisibilityController:
    """Saves the true previous visibility and restores it exactly.

    Adapters return ``None`` for an object that no longer exists; such an object is
    skipped instead of being invented, and skipped names are reported.
    """

    def __init__(self, get_visibility: Callable[[str], Optional[bool]],
                 set_visibility: Callable[[str, bool], bool]):
        self._get = get_visibility
        self._set = set_visibility
        self.saved: Dict[str, bool] = {}
        self.failed: List[str] = []
        self.active = False

    def isolate(self, visible: Iterable[str], hidden: Iterable[str]) -> Dict[str, bool]:
        if self.active:
            self.restore()
        self.failed = []
        self.saved = {}
        for name in list(visible) + list(hidden):
            if name in self.saved:
                continue
            value = self._get(name)
            if value is None:
                self.failed.append(name)
                continue
            self.saved[name] = bool(value)
        for name in visible:
            if name in self.saved and not self._set(name, True):
                self.failed.append(name)
        for name in hidden:
            if name in self.saved and not self._set(name, False):
                self.failed.append(name)
        self.active = True
        return dict(self.saved)

    def restore(self) -> Dict[str, bool]:
        saved, self.saved = self.saved, {}
        for name, value in saved.items():
            if not self._set(name, value):
                self.failed.append(name)
        self.active = False
        return saved


# --------------------------------------------------------------------------- #
# Native bridge
# --------------------------------------------------------------------------- #
class SameEventCallbackError(RuntimeError):
    """The Coin callback token could not be installed or removed."""


class CoinEventBridge:
    """Installs the real view event callback and classifies its events.

    Two real FreeCAD contracts are supported:

    * ``addEventCallbackPivy(typeId, handler)``: the handler receives a pivy callback
      node; the Coin event is ``node.getEvent()`` and the event is consumed with
      ``node.setHandled()``.  This is the preferred path and is what
      ``removeEventCallbackSWIG(typeId, token)`` removes.
    * ``addEventCallback("SoEvent", handler)``: FreeCAD delivers a *dictionary*; the
      event is read from its event entry and consumption is signalled by returning
      true (and, best effort, by setting the dictionary's handled flag).

    ``dispatch`` is the single classification point.  ``source`` distinguishes a real
    Coin delivery (``"coin"``) from a synthesized event a probe sends through Qt
    (``"synthesized"``); it never feeds back into the action.
    """

    def __init__(self, handler: Optional[Callable] = None):
        self.handler = handler
        self.view = None
        self.callbacks: List[Tuple[str, Any, Any]] = []
        self.mode: Optional[str] = None
        self.attached = False
        self.last_event: Optional[Dict[str, Any]] = None
        self.last_event_source: Optional[str] = None
        self.synthetic_input = False

    def attach(self, view) -> Any:
        if view is None:
            raise SameEventCallbackError("no active 3D view is available for the event callback")
        if self.attached and self.view is view:
            return self.callbacks
        if self.attached:
            self.detach()
        self.view = view
        self.callbacks = []
        coin = None
        try:
            from pivy import coin as coin_module
            coin = coin_module
        except Exception:  # noqa: BLE001 - the plain-dictionary API is the fallback
            coin = None
        pivy = coin is not None and hasattr(view, "addEventCallbackPivy")
        if pivy:
            for event_class in (coin.SoMouseButtonEvent, coin.SoKeyboardEvent):
                type_id = event_class.getClassTypeId()
                token = view.addEventCallbackPivy(type_id, self._pivy_dispatch)
                if token is None:
                    self.detach()
                    raise SameEventCallbackError(
                        "the 3D view refused the pivy event callback")
                self.callbacks.append(("pivy", type_id, token))
            self.mode = "pivy"
        elif hasattr(view, "addEventCallback"):
            token = view.addEventCallback("SoEvent", self._dict_dispatch)
            if token is None:
                self.view = None
                raise SameEventCallbackError("the 3D view refused the SoEvent callback")
            self.callbacks.append(("dict", "SoEvent", token))
            self.mode = "dict"
        else:
            self.view = None
            raise SameEventCallbackError(
                "the 3D view exposes neither addEventCallbackPivy nor addEventCallback")
        self.attached = True
        return self.callbacks

    def detach(self):
        view, callbacks = self.view, self.callbacks
        self.view = None
        self.callbacks = []
        self.mode = None
        self.attached = False
        if view is None or not callbacks:
            return
        errors = []
        for mode, type_id, token in callbacks:
            try:
                if mode == "pivy" and hasattr(view, "removeEventCallbackSWIG"):
                    view.removeEventCallbackSWIG(type_id, token)
                else:
                    view.removeEventCallback(type_id, token)
            except Exception as error:  # noqa: BLE001 - collected, then raised
                errors.append(str(error))
        if errors:
            raise SameEventCallbackError(
                "the event callback could not be removed: " + "; ".join(errors))

    def _pivy_dispatch(self, callback_node):
        event = None
        if hasattr(callback_node, "getEvent"):
            try:
                event = callback_node.getEvent()
            except Exception:  # noqa: BLE001 - reported as an unclassifiable event
                event = None
        return self._consume(event, argument=callback_node)

    def _dict_dispatch(self, argument):
        return self._consume(event_from_argument(argument), argument=argument)

    def _consume(self, event, *, argument):
        info = self.dispatch(event, source="synthesized" if self.synthetic_input else "coin")
        if info is None or info.get("action") is None or self.handler is None:
            return None
        if self.handler(info, event) is not True:
            return None
        if hasattr(argument, "setHandled"):
            try:
                argument.setHandled()
            except Exception:  # noqa: BLE001 - consumption is still reported
                pass
        elif isinstance(argument, dict):
            try:
                argument["Handled"] = True
            except Exception:  # noqa: BLE001
                pass
        return True

    def dispatch(self, event, *, source: str = "coin") -> Optional[Dict[str, Any]]:
        info = classify_coin_event(event)
        if info is not None:
            info["source"] = source
            info["synthetic"] = bool(self.synthetic_input) or source != "coin"
            self.last_event = info
            self.last_event_source = info["source"]
        return info


def event_from_argument(argument) -> Any:
    """Extract a Coin event from either callback contract (pivy node or dict)."""
    if argument is None:
        return None
    if hasattr(argument, "getEvent"):
        try:
            return argument.getEvent()
        except Exception:  # noqa: BLE001 - treated as no event
            return None
    if isinstance(argument, dict):
        for key in ("Event", "event", "SoEvent"):
            value = argument.get(key)
            if value is not None:
                return value
        return None
    if hasattr(argument, "getTypeId"):
        return argument
    return None


def classify_coin_event(event) -> Optional[Dict[str, Any]]:
    """Classify one Coin event without importing Coin at module import time."""
    if event is None:
        return None
    event_type = event.getTypeId()
    raw_name = event_type.getName() if hasattr(event_type, "getName") else event_type
    # pivy returns a SWIG name object whose iteration is broken; str() gives the real
    # "SoMouseButtonEvent"/"SoKeyboardEvent" text used below.
    name = str(raw_name)
    constants = type(event)
    down = getattr(constants, "DOWN", None)
    if "Keyboard" in name:
        state = event.getState() if hasattr(event, "getState") else None
        if down is not None and state is not None and state != down:
            return None
        key = event.getKey() if hasattr(event, "getKey") else None
        escape = getattr(constants, "ESCAPE", None)
        if escape is not None and key == escape:
            return {"action": "escape", "position": None, "consume": True}
        return {"action": None, "position": None, "consume": False}
    if "MouseButton" in name:
        state = event.getState() if hasattr(event, "getState") else None
        if down is not None and state is not None and state != down:
            return None
        button = event.getButton() if hasattr(event, "getButton") else None
        position = None
        if hasattr(event, "getPosition"):
            raw = event.getPosition()
            try:
                position = (int(raw[0]), int(raw[1]))
            except (TypeError, IndexError, ValueError):
                position = None
        # FreeCAD maps the Qt middle button to Coin BUTTON3 and the Qt right button to
        # Coin BUTTON2, so only BUTTON3 may confirm; BUTTON2 is an explicit right click
        # that must never reach the machine and is never consumed.
        if button == getattr(constants, "BUTTON1", 1):
            return {"action": "left", "position": position, "consume": False}
        if button == getattr(constants, "BUTTON3", 3):
            return {"action": "middle", "position": position, "consume": True}
        if button == getattr(constants, "BUTTON2", 2):
            return {"action": "right", "position": position, "consume": False}
        return {"action": None, "position": position, "consume": False}
    return {"action": None, "position": None, "consume": False}


class SelectionObserver:
    """The real ``Gui.Selection`` observer. Delegates every callback to the coordinator.

    FreeCAD passes documents/objects either as names or as objects depending on the
    callback and version, so every argument is normalised to names here and the
    ``*args`` form keeps the observer working across those signatures.
    """

    def __init__(self, coordinator: "InteractionCoordinator"):
        self.coordinator = coordinator

    def addSelection(self, *args):
        doc_name, object_name, sub_names = _selection_args(args)
        self.coordinator.on_add_selection(doc_name, object_name, sub_names)

    def removeSelection(self, *args):
        doc_name, object_name, _sub = _selection_args(args)
        self.coordinator.on_remove_selection(doc_name, object_name)

    def setPreselection(self, *args):
        doc_name, object_name, sub_names = _selection_args(args)
        self.coordinator.on_preselection(doc_name, object_name, sub_names)

    def removePreselection(self, *args):
        self.coordinator.on_preselection("", "", ())

    def clearSelection(self, *args):
        self.coordinator.on_clear_selection()


class DocumentCloseObserver:
    """FreeCAD document observer; cancels the temporary session on document close."""

    def __init__(self, on_deleted: Callable[[Optional[str]], None]):
        self._on_deleted = on_deleted

    def slotDeletedDocument(self, doc):
        self._on_deleted(getattr(doc, "Name", None))


def _split_subs(sub_name) -> Tuple[str, ...]:
    if not sub_name:
        return ()
    if isinstance(sub_name, (list, tuple)):
        return tuple(str(part) for part in sub_name if part)
    return tuple(part for part in str(sub_name).split(",") if part)


def _widget_class(widget) -> Optional[str]:
    if widget is None:
        return None
    try:
        return str(widget.metaObject().className())
    except Exception:  # noqa: BLE001
        return type(widget).__name__


def map_local_to_coin(local_xy, receiver_size, view_size):
    """Pure coordinate contract for the drawable.

    ``local_xy`` is a point in drawable-logical pixels (top-left origin),
    ``receiver_size`` the drawable's logical ``(width, height)`` and ``view_size`` the
    device``(width, height)`` returned by ``view.getSize()`` (DPI scaling).  Returns the
    Coin/device bottom-up pixel used by ``getObjectInfo``/``_pick``.
    """
    local_x, local_y = int(local_xy[0]), int(local_xy[1])
    width, height = int(receiver_size[0]), int(receiver_size[1])
    view_width, view_height = int(view_size[0]), int(view_size[1])
    if width <= 0 or height <= 0 or view_width <= 0 or view_height <= 0:
        return None
    device_x = int(round(local_x * view_width / width))
    device_y = int(round(local_y * view_height / height))
    return (max(0, min(view_width - 1, device_x)),
            max(0, min(view_height - 1, view_height - 1 - device_y)))


def map_coin_to_local(coin, receiver_size, view_size):
    """Inverse of :func:`map_local_to_coin`: Coin device pixel -> drawable-local pixel.

    Returns ``None`` when the device pixel has no drawable-local image instead of
    clamping, so a caller can require an exact round trip (the probe does) rather than
    injecting a position that the native filter would map somewhere else.
    """
    device_x, device_y = int(coin[0]), int(coin[1])
    width, height = int(receiver_size[0]), int(receiver_size[1])
    view_width, view_height = int(view_size[0]), int(view_size[1])
    if width <= 0 or height <= 0 or view_width <= 0 or view_height <= 0:
        return None
    if not (0 <= device_x < view_width and 0 <= device_y < view_height):
        return None
    # map_local_to_coin returns ``view_height - 1 - round(local_y * view_height / height)``;
    # invert that exact expression instead of re-deriving a different convention.
    top_down_device_y = view_height - 1 - device_y
    local_x = int(round(device_x * width / view_width))
    local_y = int(round(top_down_device_y * height / view_height))
    if not (0 <= local_x < width and 0 <= local_y < height):
        return None
    return (local_x, local_y)


def map_between_widgets(local_xy, to_global, from_global):
    """Map a receiver-local point through global screen coordinates to the drawable.

    ``to_global(x, y) -> (gx, gy)`` and ``from_global(gx, gy) -> (dx, dy)``.  Returns
    ``((dx, dy), (dx - x, dy - y))`` or ``None`` when either step fails.  This is the only
    mapping used for a QWidget receiver: ``QWidget.mapTo`` is invalid (and warns
    ``parent must be in parent hierarchy``) when the receiver is an ANCESTOR of the
    drawable, which is exactly the real ``Gui::View3DInventorViewer`` -> ``QOpenGLWidget``
    case.  The global round trip is valid in both directions and is never clamped.
    """
    x, y = int(local_xy[0]), int(local_xy[1])
    try:
        gx, gy = to_global(x, y)
        dx, dy = from_global(gx, gy)
    except Exception:  # noqa: BLE001 - fail closed, never a raw fallback
        return None
    return (int(dx), int(dy)), (int(dx) - x, int(dy) - y)


def point_inside(local_xy, drawable_size) -> bool:
    """True only when a drawable-local point is strictly inside the drawable."""
    if local_xy is None:
        return False
    x, y = int(local_xy[0]), int(local_xy[1])
    width, height = int(drawable_size[0]), int(drawable_size[1])
    return 0 <= x < width and 0 <= y < height


def middle_ownership(*, local_xy, drawable_size, machine_active, is_middle,
                     receiver_in_view) -> Tuple[bool, str]:
    """The single ownership decision for the active middle filter.

    Ownership requires ALL of: a middle button, an active pick session, a receiver
    inside the view subtree, and a drawable-local point strictly inside the drawable
    (never a clamped or raw fallback point).  Anything else is inspected but not owned.
    """
    if not is_middle:
        return False, "not_middle"
    if not machine_active:
        return False, "inactive"
    if not receiver_in_view:
        return False, "receiver_outside_view"
    if local_xy is None:
        return False, "no_local_point"
    if not point_inside(local_xy, drawable_size):
        return False, "point_outside_drawable"
    return True, "owned"


# Outcomes of the paired middle press/release decision (see :func:`middle_action`).
MIDDLE_PASS = "pass"
MIDDLE_OWN_PRESS = "own_press"
MIDDLE_CONFIRM = "confirm"


def middle_action(*, phase, owned, has_press, mapped):
    """The single consume/confirm decision for one middle event.

    ``phase`` is ``"press"`` or ``"release"``, ``owned`` the :func:`middle_ownership`
    verdict for THIS event, ``has_press`` whether an owned press is currently paired, and
    ``mapped`` the Coin position mapped from the *paired press* (``None`` when it could
    not be mapped inside the drawable).  An unowned press is never remembered, a release
    without an owned paired press is never confirmed and always passes through to the
    native CAD navigation, and only a paired release whose press mapped inside the view
    may confirm.
    """
    if not owned:
        return MIDDLE_PASS, "press_not_owned" if phase == "press" else "release_not_owned"
    if phase == "press":
        if mapped is None:
            return MIDDLE_PASS, "press_unmapped"
        return MIDDLE_OWN_PRESS, "press_paired"
    if not has_press:
        return MIDDLE_PASS, "unpaired_release"
    if mapped is None:
        return MIDDLE_PASS, "release_unmapped"
    return MIDDLE_CONFIRM, "release_paired"


def _selection_args(args) -> Tuple[str, str, Tuple[str, ...]]:
    """Normalise a Selection observer argument list to ``(doc, object, subs)``."""
    doc = args[0] if len(args) > 0 else ""
    obj = args[1] if len(args) > 1 else ""
    sub = args[2] if len(args) > 2 else ""
    doc_name = doc if isinstance(doc, str) else getattr(doc, "Name", "") or ""
    if not doc_name and obj is not None and not isinstance(obj, str):
        document = getattr(obj, "Document", None)
        doc_name = getattr(document, "Name", "") or ""
    object_name = obj if isinstance(obj, str) else getattr(obj, "Name", "") or ""
    if not object_name and obj is not None and not isinstance(obj, str):
        object_name = str(obj)
    return str(doc_name), str(object_name), _split_subs(sub)


class InteractionCoordinator:
    """Owns the machine, the resolver, the observers and the panel side effects."""

    def __init__(self, panel):
        self.panel = panel
        language = getattr(panel, "language", None) or i18n.DEFAULT_SOURCE
        self.machine = SelectionMachine(language=language)
        self.bridge = CoinEventBridge(handler=self.on_coin_event)
        self.visibility = VisibilityController(self._get_visibility, self._set_visibility)
        # Hidden objects must also become unselectable, otherwise FreeCAD's own click
        # picker can still select an invisible face over the visible coupling edge.
        self.selectable = VisibilityController(self._get_selectable, self._set_selectable)
        self.selection_observer = SelectionObserver(self)
        self.document_observer = DocumentCloseObserver(self.on_document_closed)
        self.view = None
        self._observers_attached = False
        self._doc_observer_attached = False
        self._applying = False
        self._doc_name: Optional[str] = None
        # Hover audit is an explicit user opt-in; it must survive a picking session but
        # never stay stale after any cleanup event.
        self.hover_audit = False
        # Objects revealed only because the table selected a hidden interface edge.
        self.revealed: Dict[str, bool] = {}
        # Last native pick (object/component/sub_names/target) for probe diagnostics.
        self.last_pick: Optional[Dict[str, Any]] = None
        self.last_selection_targets: Any = None
        # Native middle event filter (owns active press+release before CAD navigation).
        self._middle_filter = None
        self._middle_filter_targets: List[Any] = []
        self._middle_widget = None
        self._middle_press = None
        # Independent Qt-filter diagnostics (receiver/point/owned press+release/mapped).
        self.middle_events: List[Dict[str, Any]] = []
        self.event_counts: Dict[str, int] = {}
        self.renderer_info: Dict[str, Any] = {}
        self.last_offset: Optional[Dict[str, Any]] = None
        # Filter install/remove history and a monotonic generation; evidence
        # (``middle_events``/``event_counts``) is NEVER reset mid-session so a re-attach
        # cannot look like a missed event.
        self.filter_generation = 0
        self.filter_history: List[Dict[str, Any]] = []
        # Thin coupling edge: how often the deferred fresh left-ray fallback accepted.
        self.edge_left_fallback_attempts = 0
        self.edge_left_fallback_hits = 0
        self.last_edge_left_fallback: Optional[Dict[str, Any]] = None

    # -- document helpers --------------------------------------------------
    def _app(self):
        import FreeCAD as App
        return App

    def _gui(self):
        import FreeCADGui as Gui
        return Gui

    def current_document(self):
        doc = getattr(self.panel, "doc", None)
        if doc is None:
            return None
        try:
            name = doc.Name
        except ReferenceError:  # a deleted doc still referenced by the panel
            return None
        try:
            app = self._app()
        except Exception:  # noqa: BLE001 - a missing GUI is reported by attach()
            return None
        try:
            if name not in app.listDocuments():
                return None
        except Exception:  # noqa: BLE001 - a failing document list is not a document
            return None
        return doc

    def current_document_name(self) -> Optional[str]:
        doc = self.current_document()
        return doc.Name if doc is not None else None

    def build_resolver(self) -> EntityResolver:
        panel = self.panel
        preview = set(getattr(self.panel, "preview_objects", {}).values())
        result = set(getattr(getattr(panel, "result_layer", None), "objects", {}).values())
        return EntityResolver(
            document=self.current_document_name(),
            entities=getattr(panel, "entities", {}) or {},
            fingerprints=getattr(panel, "display_fingerprints", {}) or {},
            preview_objects=preview,
            result_objects=result,
            subedge_matcher=self._match_subedge,
            language=self._lang())

    def _match_subedge(self, raw: RawSelection, face_target: Target) -> Optional[Target]:
        try:
            resolved = self.panel._match_face_subelement(raw.object, face_target.ident,
                                                         raw.sub_names)
        except ValueError as error:
            raise InteractionRefusal(
                "ambiguous_subedge", self._tr("interaction.log.subedge_detail"),
                key="interaction.log.subedge_detail") from error
        if resolved is None:
            return None
        return Target(kind=resolved["target_kind"], ident=int(resolved["target_id"]),
                      object=raw.object, side=resolved.get("side"),
                      sub_names=tuple(raw.sub_names))

    def _raw_selection(self, doc_name, object_name, sub_names) -> RawSelection:
        doc_name = doc_name if isinstance(doc_name, str) else getattr(doc_name, "Name", "") or ""
        object_name = (object_name if isinstance(object_name, str)
                       else getattr(object_name, "Name", "") or "")
        app = self._app()
        doc = app.getDocument(doc_name) if doc_name else None
        obj = doc.getObject(object_name) if doc is not None and object_name else None
        kind = getattr(obj, "IbraKind", None) if obj is not None else None
        ident = getattr(obj, "IbraBrepId", None) if obj is not None else None
        fingerprint = None
        if obj is not None:
            try:
                fingerprint = (obj.Shape.hashCode(), str(obj.Placement))
            except Exception:  # noqa: BLE001 - a shapeless object has no fingerprint
                fingerprint = None
        return RawSelection(document=doc_name or "", object=object_name or "",
                            kind=kind, ident=ident, sub_names=tuple(sub_names),
                            fingerprint=fingerprint)

    # -- lifecycle ---------------------------------------------------------
    def _ensure_document_observer(self):
        """Register the close observer independently of a picking session."""
        if self._doc_observer_attached:
            return
        try:
            self._app().addDocumentObserver(self.document_observer)
            self._doc_observer_attached = True
        except Exception as error:  # noqa: BLE001 - reported, never hidden
            self._log(self._tr("interaction.log.doc_observer_failed", error=error))

    def _remove_document_observer(self):
        if not self._doc_observer_attached:
            return
        self._doc_observer_attached = False
        try:
            self._app().removeDocumentObserver(self.document_observer)
        except Exception as error:  # noqa: BLE001 - reported, never hidden
            self._log(self._tr("interaction.log.doc_observer_remove_failed", error=error))

    def attach(self) -> bool:
        """Install the observer once, but always re-arm the CURRENT view and Qt filter.

        A new picking session, a document transition or an active-renderer replacement
        must re-point the Coin callback and middle filter at the live view; returning
        early while ``_observers_attached`` left them bound to a stale renderer.
        """
        if not self._observers_attached:
            gui = self._gui()
            gui.Selection.addObserver(self.selection_observer)
            self._ensure_document_observer()
            self._observers_attached = True
        self._doc_name = self.current_document_name()
        self._attach_view()   # always re-acquire the live view and re-install the filter
        return True

    def _attach_view(self):
        gui = self._gui()
        view = None
        try:
            active = gui.activeDocument()
            view = active.activeView() if active is not None else None
        except Exception:  # noqa: BLE001 - reported through the guidance line
            view = None
        if view is None:
            self.view = None
            return False
        try:
            self.bridge.attach(view)
        except SameEventCallbackError as error:
            self.view = None
            self._log(self._tr("interaction.log.callback_unavailable", error=error))
            return False
        self.view = view
        # The Qt filter owns the active MiddleButton before CAD navigation; the pivy
        # callback stays as a fallback (and never double-handles while the filter is on).
        self.remove_middle_filter()
        self.install_middle_filter()
        return True

    def detach(self, *, restore: bool = True):
        if restore:
            self.restore_visibility()
        self.remove_middle_filter()
        if not self._observers_attached:
            self._remove_document_observer()
            return
        errors = []
        gui = self._gui()
        try:
            gui.Selection.removeObserver(self.selection_observer)
        except Exception as error:  # noqa: BLE001 - collected, then surfaced
            errors.append(f"Selection observer: {error}")
        try:
            self.bridge.detach()
        except SameEventCallbackError as error:
            errors.append(str(error))
        self._observers_attached = False
        self._remove_document_observer()
        self.view = None
        if errors:
            self._log(self._tr("interaction.log.callback_cleanup",
                               errors="；".join(errors)))

    # -- entry points ------------------------------------------------------
    def _lang(self):
        return getattr(self.machine, "language", i18n.DEFAULT_SOURCE)

    def _tr(self, key, **params):
        return i18n.translate(key, self._lang(), **params)

    def set_language(self, language):
        """Re-render the current mouse guidance/refusal in the new language.

        The machine state (mode, step, targets, candidate edges), the observers and
        the native selection are untouched: only the displayed text changes.
        """
        self.machine.set_language(language)
        self._refresh()
        return self.machine.language

    def set_hover_mode(self, mode: str):
        outcome = self.machine.set_hover_mode(mode)
        self.apply(outcome)

    def start_boundary(self, category: str):
        if self.current_document() is None:
            self._refuse(InteractionRefusal(
                "no_document", self._tr("interaction.refusal.no_document"),
                key="interaction.refusal.no_document"))
            return
        self.machine.set_couplings(self._coupling_records())
        outcome = self.machine.start_boundary(category, pressure=self.pressure_mode())
        self.attach()
        self.apply(outcome)

    def start_coupling(self):
        if self.current_document() is None:
            self._refuse(InteractionRefusal(
                "no_document", self._tr("interaction.refusal.no_document"),
                key="interaction.refusal.no_document"))
            return
        self.machine.set_couplings(self._coupling_records())
        outcome = self.machine.start_coupling()
        self.attach()
        self.apply(outcome)

    def cancel(self, *, reason: str = "cancel"):
        outcome = self.machine.escape()
        if reason == "page_switch":
            outcome.guidance_key = "interaction.guidance.page_switch"
            outcome.guidance = self._tr(outcome.guidance_key)
        elif reason == "import":
            outcome.guidance_key = "interaction.guidance.import"
            outcome.guidance = self._tr(outcome.guidance_key)
        elif reason == "document_closed":
            outcome.guidance_key = "interaction.guidance.doc_closed"
            outcome.guidance = self._tr(outcome.guidance_key)
            self.visibility.saved = {}
            self.visibility.active = False
            self.selectable.saved = {}
            self.selectable.active = False
            self.machine.reset()
        self.hover_audit = False
        self.hide_revealed()
        self.detach(restore=(reason != "document_closed"))
        self.apply(outcome)

    def set_hover_audit(self, enabled: bool):
        """Explicit hover-audit toggle; any cleanup clears it again."""
        self.hover_audit = bool(enabled)
        if self.hover_audit:
            if self.attach():
                self._refresh()
        elif not self.machine.active():
            self.cancel(reason="cancel")
        else:
            self._refresh()

    def on_page_switch(self):
        self.cancel(reason="page_switch")

    def on_import(self):
        self.cancel(reason="import")

    def on_document_closed(self, name: Optional[str]):
        if name is None or name == self._doc_name or name == self.current_document_name():
            self.cancel(reason="document_closed")

    def shutdown(self):
        self.cancel(reason="cancel")

    def reset_silently(self):
        self.machine.mode = "idle"
        self.machine.targets = []
        self.machine.hover_text = None
        self.hover_audit = False
        self.hide_revealed()
        self.detach(restore=True)

    def pressure_mode(self) -> bool:
        checker = getattr(self.panel, "_pressure_mode", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception as error:  # noqa: BLE001 - reported, not hidden
                self._log(self._tr("interaction.log.pressure_mode_failed", error=error))
        return False

    # -- coupling-only visibility -----------------------------------------
    def _is_group_container(self, obj) -> bool:
        """Group/container objects are the only ones never hidden by coupling-only."""
        type_id = str(getattr(obj, "TypeId", ""))
        if "DocumentObjectGroup" in type_id or "GeoFeatureGroup" in type_id or "Origin" in type_id:
            return True
        return hasattr(obj, "Group") and not hasattr(obj, "Shape")

    def _drawable_objects(self, doc):
        objects = []
        try:
            raw = list(getattr(doc, "Objects", []) or [])
        except Exception:  # noqa: BLE001 - a vanished document has no objects
            return objects
        for obj in raw:
            try:
                if getattr(obj, "ViewObject", None) is None:
                    continue
                if self._is_group_container(obj):
                    continue
                objects.append(obj)
            except ReferenceError:  # a deleted object still listed
                continue
        return objects

    def toggle_coupling_visibility(self):
        if self.visibility.active:
            self.restore_visibility()
            if not self._observers_attached:
                self._remove_document_observer()
            self.machine.set_notice(None)
            self.machine.set_message("interaction.guidance.restored")
            self.apply(self.machine.current_outcome())
            return
        doc = self.current_document()
        if doc is None:
            self._refuse(InteractionRefusal(
                "no_document", self._tr("interaction.refusal.no_document"),
                key="interaction.refusal.no_document"))
            return
        # Isolation alone must still clean up when the document closes, so the close
        # observer is installed without any picking/navigation hook.
        self._ensure_document_observer()
        coupling_names = {name for (kind, _ident), name in (self.panel.entities or {}).items()
                          if kind == KIND_COUPLING and name}
        visible, hidden = [], []
        for obj in self._drawable_objects(doc):
            name = getattr(obj, "Name", None)
            if not name:
                continue
            (visible if name in coupling_names else hidden).append(name)
        saved = self.visibility.isolate(visible, hidden)
        self.selectable.isolate(visible, hidden)
        self.machine.set_message("interaction.guidance.isolated", count=len(visible))
        if self.visibility.failed:
            self.machine.set_notice("interaction.notice.isolate_skipped",
                                    count=len(self.visibility.failed))
            self._log(self._tr("interaction.log.isolate_skipped")
                      + ", ".join(self.visibility.failed))
        self.apply(self.machine.current_outcome())
        return saved

    def restore_visibility(self):
        if self.visibility.active or self.visibility.saved:
            before = len(self.visibility.failed)
            self.visibility.restore()
            skipped = self.visibility.failed[before:]
            if skipped:
                self._log(self._tr("interaction.log.restore_skipped") + ", ".join(skipped))
        if self.selectable.active or self.selectable.saved:
            self.selectable.restore()

    def _get_visibility(self, name: str) -> Optional[bool]:
        doc = self.current_document()
        if doc is None:
            return None
        obj = doc.getObject(name)
        view = getattr(obj, "ViewObject", None) if obj is not None else None
        if view is None:
            return None
        try:
            return bool(view.Visibility)
        except Exception:  # noqa: BLE001 - a view without the property
            return None

    def _set_visibility(self, name: str, value: bool) -> bool:
        doc = self.current_document()
        if doc is None:
            return False
        obj = doc.getObject(name)
        view = getattr(obj, "ViewObject", None) if obj is not None else None
        if view is None:
            return False
        try:
            view.Visibility = bool(value)
        except Exception:  # noqa: BLE001 - reported through the failed list
            return False
        return True

    def _get_selectable(self, name: str) -> Optional[bool]:
        doc = self.current_document()
        if doc is None:
            return None
        obj = doc.getObject(name)
        view = getattr(obj, "ViewObject", None) if obj is not None else None
        if view is None:
            return None
        try:
            return bool(view.Selectable)
        except Exception:  # noqa: BLE001 - a view without the property
            return None

    def _set_selectable(self, name: str, value: bool) -> bool:
        doc = self.current_document()
        if doc is None:
            return False
        obj = doc.getObject(name)
        view = getattr(obj, "ViewObject", None) if obj is not None else None
        if view is None:
            return False
        try:
            view.Selectable = bool(value)
        except Exception:  # noqa: BLE001 - reported through the failed list
            return False
        return True

    def reveal_object(self, name: str) -> bool:
        """Temporarily show one hidden object (table-selected interface edge).

        The pre-existing value is remembered so :meth:`hide_revealed` restores it; it is
        never a persistent visibility change.
        """
        if not name or name in self.revealed:
            return False
        value = self._get_visibility(name)
        if value is None or value:
            return False
        if not self._set_visibility(name, True):
            return False
        self.revealed[name] = bool(value)
        return True

    def hide_revealed(self) -> Dict[str, bool]:
        revealed, self.revealed = self.revealed, {}
        if self.visibility.active:
            # An active coupling-only isolation owns the visibility: restoring a
            # temporarily revealed edge here would hide it again under the isolation.
            return revealed
        failed = []
        for name, value in revealed.items():
            if not self._set_visibility(name, value):
                failed.append(name)
        if failed:
            self._log(self._tr("interaction.log.reveal_restore_failed") + ", ".join(failed))
        return revealed

    def on_coupling_row_selected(self, edge_id: Optional[int]):
        """Table-driven highlight: select the interface edge, revealing it reversibly."""
        if edge_id is None:
            self.hide_revealed()
            self._write_selection(clear_only=True)
            return
        self.hide_revealed()
        name = (self.panel.entities or {}).get((KIND_COUPLING, int(edge_id)))
        if not name:
            return
        revealed = self.reveal_object(name)
        self._write_selection(objects=[{"object": name, "sub_names": []}])
        if revealed:
            self.machine.set_notice("interaction.notice.revealed", edge=int(edge_id))
        self._refresh()

    def _write_selection(self, *, objects=(), clear_only: bool = False):
        """One-way programmatic selection write with the observer safely removed."""
        gui = self._gui()
        doc_name = self.current_document_name()
        suspended = self._suspend_selection_observer()
        self._applying = True
        try:
            gui.Selection.clearSelection()
            if not clear_only:
                for entry in objects:
                    if doc_name and entry.get("object"):
                        gui.Selection.addSelection(doc_name, entry["object"],
                                                   ",".join(entry.get("sub_names") or []))
        finally:
            self._applying = False
            if suspended:
                self._resume_selection_observer()

    # -- native observer callbacks ----------------------------------------
    def on_add_selection(self, doc_name, object_name, sub_names):
        if self._applying or not self.machine.active():
            return
        # NEVER mutate the native selection from inside its own observer: the machine
        # only records the picked target here (mutate_selection=False), so a delayed
        # native notification cannot re-enter this callback in a loop.
        self._handle_target(self._raw_selection(doc_name, object_name, sub_names),
                            middle=False, from_observer=True)

    def on_remove_selection(self, doc_name, object_name):
        if self._applying or not self.machine.active():
            return
        self.machine.targets = [entry for entry in self.machine.targets
                                if entry.object != object_name]
        self._refresh()

    def on_clear_selection(self):
        if self._applying:
            return
        self.machine.targets = []
        self.machine.hover_text = None
        self._refresh()

    def on_preselection(self, doc_name, object_name, sub_names):
        if self._applying:
            return
        if not object_name:
            self.machine.hover(None)
            self._refresh()
            return
        raw = self._raw_selection(doc_name, object_name, sub_names)
        try:
            target = self.build_resolver().resolve(raw)
        except InteractionRefusal as error:
            self.machine.hover(None)
            self.machine.adopt_notice(refusal_outcome(error, self._lang()))
            self._refresh()
            return
        self.machine.hover(target)
        if self.machine.notice:
            self.machine.set_notice(None)
        self._refresh()

    def _handle_target(self, raw: RawSelection, *, middle: bool, from_observer: bool = False):
        try:
            target = self.build_resolver().resolve(raw)
        except InteractionRefusal as error:
            self._refuse(error)
            return
        if middle:
            outcome = self.machine.middle(target)
        else:
            additive = self._selection_count() > 1
            self.machine.set_pressure(self.pressure_mode())
            outcome = self.machine.left(target, additive=additive)
        # Inside the Selection observer the native selection is already exactly what the
        # user picked; writing it again would feed delayed notifications back in.
        self.apply(outcome, mutate_selection=not from_observer)

    def _suspend_selection_observer(self) -> bool:
        """Remove the Selection observer around a one-way programmatic selection write."""
        if not self._observers_attached:
            return False
        try:
            self._gui().Selection.removeObserver(self.selection_observer)
        except Exception as error:  # noqa: BLE001 - reported, then the write is skipped
            self._log(self._tr("interaction.log.observer_pause_failed", error=error))
            return False
        return True

    def _resume_selection_observer(self):
        try:
            self._gui().Selection.addObserver(self.selection_observer)
        except Exception as error:  # noqa: BLE001 - reported, never hidden
            self._log(self._tr("interaction.log.observer_resume_failed", error=error))

    def _selection_count(self) -> int:
        try:
            return len(self._gui().Selection.getSelectionEx())
        except Exception:  # noqa: BLE001 - an unavailable selection is treated as single
            return 1

    def _native_selection_targets(self) -> List[Target]:
        """Resolve the registered targets currently held by the native Selection."""
        targets = []
        diagnostics = []
        try:
            selections = self._gui().Selection.getSelectionEx()
        except Exception as error:  # noqa: BLE001 - an unavailable selection yields nothing
            self.last_selection_targets = {"error": f"{type(error).__name__}: {error}"}
            return targets
        for selection in selections or ():
            obj = getattr(selection, "Object", None)
            if obj is None:
                continue
            document = getattr(obj, "Document", None)
            raw = self._raw_selection(getattr(document, "Name", ""), getattr(obj, "Name", ""),
                                      tuple(getattr(selection, "SubElementNames", ()) or ()))
            try:
                targets.append(self.build_resolver().resolve(raw))
            except InteractionRefusal as error:
                diagnostics.append({"object": raw.object, "subs": list(raw.sub_names),
                                    "refused": error.code, "message": error.message})
            except Exception as error:  # noqa: BLE001 - reported, never silently empty
                diagnostics.append({"object": raw.object, "subs": list(raw.sub_names),
                                    "error": f"{type(error).__name__}: {error}"})
        self.last_selection_targets = diagnostics or [target.key() for target in targets]
        return targets

    def _pending_keys(self) -> set:
        keys = {target.key() for target in self.machine.targets}
        if self.machine.mode == "coupling" and self.machine.candidate_edge is not None:
            keys.add((KIND_COUPLING, int(self.machine.candidate_edge), None))
        return keys

    # -- Coin callback -----------------------------------------------------
    def on_coin_event(self, info, event):
        """Act on one classified event; returns True only for events we consume."""
        if info is None or info.get("action") is None:
            return None
        if info["action"] == "escape":
            if not self.machine.active():
                return None
            self.cancel(reason="cancel")
            return True
        if info["action"] == "left":
            # A native left click normally reaches us through the Selection observer.
            # On a thin coupling polyline FreeCAD's own picker can omit the addSelection
            # notification, so during the coupling edge step only, resolve the ACTUAL
            # event position freshly after the observation turn.  This never consumes the
            # event, never uses a cached target, and a face duplicate cannot satisfy it.
            if (self.machine.mode == "coupling" and self.machine.step == "edge"
                    and not self.machine.edge_picked):
                self._schedule_edge_left(info.get("position"))
            return None
        if info["action"] == "middle":
            # When the Qt event filter owns the active middle press/release it consumes
            # both before CAD navigation, so the pivy path must not handle it again.
            if self._middle_filter is not None or not self.machine.active():
                return None
            return True if self.on_middle_event(info.get("position")) else None
        return None

    def _schedule_edge_left(self, position):
        """Run :meth:`_edge_left_fallback` after the native selection turn when possible."""
        if position is None:
            return
        try:
            from PySide import QtCore
        except ImportError:
            try:
                from PySide6 import QtCore
            except ImportError:
                QtCore = None
        if QtCore is not None:
            QtCore.QTimer.singleShot(0, lambda: self._edge_left_fallback(position))
        else:
            self._edge_left_fallback(position)

    def _edge_left_fallback(self, position):
        """Fresh Coin ray fallback for a thin coupling edge the observer did not accept.

        Only a still-unpicked coupling edge step, and only a fresh ``_pick`` that
        resolves to one of the candidate coupling edges, may accept it.  A face (e.g. the
        second patch), an unrelated coupling edge, or a refused/stale pick changes
        nothing.
        """
        if (self.machine.mode != "coupling" or self.machine.step != "edge"
                or self.machine.edge_picked or position is None):
            return False
        self.edge_left_fallback_attempts += 1
        # The deferred callback can run after the view binding was cleared (document /
        # renderer transition); re-acquire the live view before the real ray pick.
        if self.view is None:
            self._attach_view()
        target = self._pick(position)
        if target is None or target.kind != KIND_COUPLING:
            return False
        if target.ident not in self.machine.candidate_edges:
            return False
        self.apply(self.machine.left(target), mutate_selection=False)
        self.edge_left_fallback_hits += 1
        self.last_edge_left_fallback = {"position": list(position),
                                        "target": [target.kind, int(target.ident)]}
        return True

    def on_middle_event(self, position, *, defer: bool = False):
        """Confirm the pending step with a FRESH pixel pick and the real resolver.

        No cached target is substituted: a wrong edge, a changed/overlapping patch or a
        stale geometry baseline must still be refused by ``_pick``/``machine.middle``.
        """
        if not self.machine.active() or position is None:
            return False
        if self.view is None:
            self._attach_view()
        target = self._pick(position)
        if target is None:
            return True   # consumed, with the refusal already surfaced

        def deliver():
            if not self.machine.active():
                return
            self.machine.set_pressure(self.pressure_mode())
            self.apply(self.machine.middle(target))

        if defer:
            try:
                from PySide import QtCore
            except ImportError:
                try:
                    from PySide6 import QtCore
                except ImportError:
                    QtCore = None
            if QtCore is not None:
                # Deferred so the capture never runs re-entrantly inside the Qt filter.
                QtCore.QTimer.singleShot(0, deliver)
                return True
        deliver()
        return True

    # -- native middle event filter ----------------------------------------
    def _renderer_widget(self, view):
        """The proven native renderer QWidget for the active view (lazy Qt import)."""
        try:
            from PySide import QtCore, QtWidgets
        except ImportError:
            from PySide6 import QtCore, QtWidgets
        main = self._gui().getMainWindow()
        try:
            mdi = main.findChild(QtWidgets.QMdiArea)
            sub = mdi.activeSubWindow() if mdi is not None else None
            root = sub.widget() if sub is not None else None
        except Exception:  # noqa: BLE001 - fall back to a class-name search
            root = None
        candidates = []
        if root is not None:
            candidates.append(root)
            candidates.extend(root.findChildren(QtWidgets.QWidget))
        candidates.extend(main.findChildren(QtWidgets.QWidget))
        # The drawable the Coin ray pick / view.getSize() refer to is the inner
        # QOpenGLWidget, not the Gui::View3DInventorViewer container (which can add a
        # border/scrollbar offset). Prefer the actual GL drawable.
        for candidate in candidates:
            try:
                name = str(candidate.metaObject().className())
            except Exception:  # noqa: BLE001
                name = type(candidate).__name__
            if "QOpenGLWidget" in name:
                return candidate
        for candidate in candidates:
            try:
                name = str(candidate.metaObject().className())
            except Exception:  # noqa: BLE001
                name = type(candidate).__name__
            if "View3DInventorViewer" in name:
                return candidate
        return None

    def _receiver_to_drawable(self, point, receiver):
        """Receiver-local point -> drawable-local point, or ``None`` (fail closed).

        A failure to read the point or to map it must never fall back to raw receiver
        coordinates.  The mapping goes through global screen coordinates because
        ``QWidget.mapTo`` is invalid when the receiver is an ancestor of the drawable
        (the real ``Gui::View3DInventorViewer`` -> ``QOpenGLWidget`` case), which makes Qt
        warn ``parent must be in parent hierarchy`` and yields a wrong offset.
        """
        drawable = self._middle_widget
        if point is None or drawable is None:
            return None, "no_point_or_drawable"
        try:
            px, py = int(round(point.x())), int(round(point.y()))
        except Exception as error:  # noqa: BLE001 - reported in the trace
            return None, f"point_error:{type(error).__name__}:{error}"
        if receiver is drawable:
            self.last_offset = {"receiver": _widget_class(receiver),
                                "drawable": _widget_class(drawable),
                                "offset": [0, 0], "receiver_local": [px, py],
                                "mapped_local": [px, py], "method": "same_widget"}
            return (px, py), "same_widget"
        try:
            from PySide import QtCore, QtWidgets
        except ImportError:
            try:
                from PySide6 import QtCore, QtWidgets
            except ImportError:
                return None, "no_qt"
        if not isinstance(receiver, QtWidgets.QWidget) or not isinstance(
                drawable, QtWidgets.QWidget):
            return None, "not_widget"

        def to_global(x, y):
            global_point = receiver.mapToGlobal(QtCore.QPoint(x, y))
            return (global_point.x(), global_point.y())

        def from_global(x, y):
            local_point = drawable.mapFromGlobal(QtCore.QPoint(x, y))
            return (local_point.x(), local_point.y())

        mapped = map_between_widgets((px, py), to_global, from_global)
        if mapped is None:
            return None, "mapFromGlobal_failed"
        (mx, my), (dx, dy) = mapped
        self.last_offset = {"receiver": _widget_class(receiver),
                            "drawable": _widget_class(drawable),
                            "offset": [dx, dy],
                            "receiver_local": [px, py],
                            "mapped_local": [mx, my], "method": "mapFromGlobal"}
        return (mx, my), "mapFromGlobal"

    def _map_middle_position(self, point, receiver):
        """Receiver-local Qt point -> Coin/device bottom-up, or ``None`` (fail closed)."""
        view = self.view
        drawable = self._middle_widget
        if view is None or point is None or drawable is None:
            return None
        local, reason = self._receiver_to_drawable(point, receiver)
        if local is None:
            self.last_offset = {"receiver": _widget_class(receiver), "reason": reason}
            return None
        try:
            size = view.getSize()
            view_width, view_height = int(size[0]), int(size[1])
            width, height = drawable.width(), drawable.height()
        except Exception:  # noqa: BLE001
            return None
        return map_local_to_coin(local, (width, height), (view_width, view_height))

    def _view_root_widget(self, widget):
        """The outermost ``Gui::View3DInventor`` ancestor of the drawable."""
        root = widget
        try:
            ancestor = widget.parent() if widget is not None else None
            while ancestor is not None:
                root = ancestor
                if "View3DInventor" in _widget_class(ancestor):
                    break
                ancestor = ancestor.parent() if hasattr(ancestor, "parent") else None
        except Exception:  # noqa: BLE001
            pass
        return root

    def install_middle_filter(self):
        """Own the active MiddleButton strictly inside the current view subtree."""
        if self._middle_filter is not None or self.view is None:
            return False
        try:
            from PySide import QtCore, QtWidgets
        except ImportError:
            from PySide6 import QtCore, QtWidgets
        widget = self._renderer_widget(self.view)
        if widget is None:
            self._log(self._tr("interaction.log.no_renderer"))
            return False
        self._middle_widget = widget
        self.filter_generation += 1
        generation = self.filter_generation
        coordinator = self
        press_type = getattr(QtCore.QEvent, "MouseButtonPress", None)
        release_type = getattr(QtCore.QEvent, "MouseButtonRelease", None)
        middle_button = getattr(QtCore.Qt, "MiddleButton", None)

        def _class_of(obj):
            return _widget_class(obj) or type(obj).__name__

        def _point_of(point):
            if point is None:
                return None
            try:
                return [int(round(point.x())), int(round(point.y()))]
            except Exception:  # noqa: BLE001
                return None

        # Ownership is restricted to the view subtree only (drawable, its descendants
        # and its Gui::View3DInventor ancestors) - never mainWindow/QApplication.
        view_root = self._view_root_widget(widget)
        try:
            view_widgets = [view_root] + view_root.findChildren(QtWidgets.QWidget)
        except Exception:  # noqa: BLE001
            view_widgets = [widget]
        view_ids = {id(candidate) for candidate in view_widgets}
        targets = list(view_widgets)

        class _MiddleFilter(QtCore.QObject):
            def eventFilter(self, obj, event):
                try:
                    etype = event.type()
                except Exception as error:  # noqa: BLE001 - traced, never swallowed
                    coordinator.middle_events.append(
                        {"generation": generation, "receiver": _class_of(obj),
                         "etype_error": f"{type(error).__name__}: {error}",
                         "owned": False, "reason": "etype_error"})
                    return False
                if etype not in (press_type, release_type):
                    return False
                phase = "press" if etype == press_type else "release"
                trace = {"phase": phase, "generation": generation,
                         "receiver": _class_of(obj), "receiver_id": id(obj),
                         "receiver_in_view": id(obj) in view_ids, "etype": repr(etype)}
                button = None
                try:
                    button = event.button() if hasattr(event, "button") else None
                    trace["button"] = repr(button)
                    trace["button_error"] = None
                except Exception as error:  # noqa: BLE001 - recorded, not swallowed
                    trace["button_error"] = f"{type(error).__name__}: {error}"
                trace["expected_middle"] = repr(middle_button)
                trace["is_middle"] = button == middle_button
                point = None
                point_error = None
                for method in ("position", "pos"):
                    if point is not None:
                        break
                    getter = getattr(event, method, None)
                    if not callable(getter):
                        continue
                    try:
                        point = getter()
                        trace["point_method"] = method
                    except Exception as error:  # noqa: BLE001 - recorded, not swallowed
                        point_error = f"{method}:{type(error).__name__}:{error}"
                trace["point_method"] = trace.get("point_method")
                trace["point_error"] = point_error
                trace["point"] = _point_of(point)
                trace["active"] = bool(coordinator.machine.active())
                trace["mode"] = coordinator.machine.mode
                trace["step"] = coordinator.machine.step
                local, local_reason = coordinator._receiver_to_drawable(point, obj)
                trace["local"] = list(local) if local else None
                trace["local_reason"] = local_reason
                try:
                    drawable_size = (coordinator._middle_widget.width(),
                                     coordinator._middle_widget.height())
                except Exception:  # noqa: BLE001
                    drawable_size = (0, 0)
                owned, reason = middle_ownership(
                    local_xy=local, drawable_size=drawable_size,
                    machine_active=trace["active"], is_middle=trace["is_middle"],
                    receiver_in_view=trace["receiver_in_view"])
                trace["owned"] = owned
                trace["reason"] = reason
                # Pairing: an owned press must map INSIDE the drawable before it is
                # remembered, and a release is confirmed only while such an owned press
                # is paired.  A release with no owned press never confirms and is never
                # given a release-only fallback position - it passes through.
                if phase == "press":
                    mapped = coordinator._map_middle_position(point, obj) if owned else None
                else:
                    press = coordinator._middle_press
                    mapped = press[2] if press is not None else None
                action, pair_reason = middle_action(
                    phase=phase, owned=owned,
                    has_press=coordinator._middle_press is not None, mapped=mapped)
                trace["mapped"] = list(mapped) if mapped else None
                trace["pair"] = pair_reason
                counters = coordinator.event_counts
                key = f"{phase}:{button}"
                counters[key] = counters.get(key, 0) + 1
                if action == MIDDLE_OWN_PRESS:
                    coordinator._middle_press = (point, obj, mapped)
                    coordinator.middle_events.append(trace)
                    return True
                coordinator._middle_press = None
                coordinator.middle_events.append(trace)
                if action == MIDDLE_CONFIRM:
                    coordinator.on_middle_event(mapped, defer=True)
                    return True
                return False

        self._middle_filter = _MiddleFilter()
        self._middle_filter_targets = []
        seen = set()
        for target in targets:
            if id(target) in seen:
                continue
            seen.add(id(target))
            try:
                target.installEventFilter(self._middle_filter)
                self._middle_filter_targets.append(target)
            except Exception:  # noqa: BLE001 - reported, never silent
                self._log(self._tr("interaction.log.filter_install_failed",
                                   target=type(target).__name__))
        try:
            parent = widget.parent()
            self.renderer_info = {
                "widget": _class_of(widget), "widget_id": id(widget),
                "widget_size": [widget.width(), widget.height()],
                "parent": _class_of(parent) if parent is not None else None,
                "view_root": _class_of(view_root), "view_id": id(self.view),
                "generation": generation,
                "filter_targets": [_class_of(target) for target in targets]}
        except Exception as error:  # noqa: BLE001 - reported
            self.renderer_info = {"error": f"{type(error).__name__}: {error}"}
        self.filter_history.append({"action": "install", "generation": generation,
                                    "reason": "attach", "targets": len(targets),
                                    "view_root": _class_of(view_root)})
        return True

    def remove_middle_filter(self):
        if self._middle_filter is None:
            self._middle_press = None
            return
        for target in self._middle_filter_targets:
            try:
                target.removeEventFilter(self._middle_filter)
            except Exception:  # noqa: BLE001 - reported, never hidden
                self._log(self._tr("interaction.log.filter_remove_failed",
                                   target=type(target).__name__))
        self.filter_history.append({"action": "remove",
                                    "generation": self.filter_generation,
                                    "targets": len(self._middle_filter_targets)})
        self._middle_filter = None
        self._middle_filter_targets = []
        self._middle_widget = None
        self._middle_press = None

    def _pending_target(self) -> Optional[Target]:
        """The single target the left selection already recorded, if unambiguous."""
        if self.machine.mode == "boundary" and len(self.machine.targets) == 1:
            return self.machine.targets[0]
        if (self.machine.mode == "coupling" and self.machine.edge_picked
                and self.machine.candidate_edge is not None):
            name = (self.panel.entities or {}).get(
                (KIND_COUPLING, int(self.machine.candidate_edge)), "")
            return Target(KIND_COUPLING, int(self.machine.candidate_edge), name)
        return None

    def _pick(self, position) -> Optional[Target]:
        view = self.view
        if view is None or position is None:
            self.last_pick = {"position": list(position) if position else None,
                              "skipped": "no_view" if view is None else "no_position"}
            return None
        coin = tuple(int(value) for value in position)
        try:
            info = view.getObjectInfo(coin)
        except Exception as error:  # noqa: BLE001 - reported, then refused cleanly
            self.last_pick = {"position": list(coin), "reason": "get_object_info_failed",
                              "error": f"{type(error).__name__}: {error}"}
            self._log(self._tr("interaction.log.pick_failed", error=error))
            return None
        if not isinstance(info, dict):
            # A no-hit pixel must still be recorded: leaving the previous successful pick
            # in place would report a stale target for this fresh attempt.
            self.last_pick = {"position": list(coin), "reason": "no_info",
                              "info_type": type(info).__name__}
            return None
        object_name = info.get("Object")
        doc_name = info.get("Document") or self.current_document_name()
        if not object_name:
            self.last_pick = {"position": list(coin), "reason": "no_object",
                              "document": doc_name, "component": info.get("Component")}
            return None
        # The Component/SubName returned by the native picker MUST travel with the
        # target: a middle click on a patch sub-edge has to resolve to the same target
        # the left selection captured, not to the whole face.
        sub_names = ()
        for key in ("SubName", "Component"):
            value = info.get(key)
            if value:
                sub_names = _split_subs(value)
                break
        self.last_pick = {"position": list(coin),
                          "document": doc_name, "object": object_name,
                          "component": info.get("Component"),
                          "sub_names": list(sub_names)}
        raw = self._raw_selection(doc_name, object_name, sub_names)
        try:
            target = self.build_resolver().resolve(raw)
        except InteractionRefusal as error:
            self._refuse(error)
            self.last_pick["refused"] = error.code
            return None
        self.last_pick["target"] = [target.kind, target.ident, target.side]
        return target

    # -- applying outcomes -------------------------------------------------
    def apply(self, outcome: Outcome, *, mutate_selection: bool = True):
        """Apply one machine outcome.  Text is adopted in the machine's language.

        Only the message *source* is stored: a later language switch re-renders the
        same step, and a later status update still wins (see
        :meth:`SelectionMachine.set_language`).
        """
        if outcome.guidance:
            if outcome.guidance_key:
                self.machine.set_message(outcome.guidance_key, **outcome.guidance_params)
            else:
                self.machine._raw_guidance = outcome.guidance
                self.machine._guidance_key = None
                self.machine.guidance = outcome.guidance
        if outcome.notice:
            self.machine.adopt_notice(outcome)
        failed_capture = None
        for event in outcome.events:
            try:
                if event.get("kind") == "capture":
                    if self._capture(event.get("category"), event.get("objects") or [],
                                     event.get("targets") or []):
                        if event.get("guidance_key"):
                            self.machine.set_message(event["guidance_key"],
                                                     **(event.get("guidance_params") or {}))
                        elif event.get("guidance_after"):
                            self.machine._guidance_key = None
                            self.machine._raw_guidance = event["guidance_after"]
                            self.machine.guidance = event["guidance_after"]
                        self.machine.set_notice(None)
                    else:
                        failed_capture = (event.get("category"),
                                          event.get("targets") or [])
                else:
                    self._apply_event(event, mutate_selection=mutate_selection)
            except Exception as error:  # noqa: BLE001 - surfaced in the panel log
                self._log(self._tr("interaction.log.apply_failed",
                                   error=f"{type(error).__name__}: {error}"))
        if failed_capture is not None:
            # A guarded capture can refuse or write a different target set; the panel
            # must never show a confirmation for something that was not captured.
            self.machine.set_message("interaction.guidance.capture_failed")
            self.machine.set_notice(
                "interaction.notice.capture_failed",
                targets=", ".join(f"{entry.get('target_kind')} {entry.get('target_id')}"
                                  for entry in failed_capture[1]))
        self._refresh()

    def _apply_event(self, event: Dict[str, Any], *, mutate_selection: bool = True):
        kind = event.get("kind")
        if kind == "highlight_targets":
            # From the Selection observer the native selection already is this target;
            # re-writing it would re-enter the observer.  Only a non-observer source
            # (none today) may touch the native selection.
            if mutate_selection:
                self._highlight_objects(event.get("objects") or [])
        elif kind in ("highlight_coupling", "highlight_coupling_candidates"):
            for edge_id in ([event["edge_id"]] if "edge_id" in event
                            else event.get("edge_ids") or []):
                self._select_coupling_row(int(edge_id), mutate_selection=mutate_selection)
        elif kind == "select_coupling_row":
            self._select_coupling_row(int(event["edge_id"]), mutate_selection=mutate_selection)
        elif kind == "expose_coupling_decision":
            self._expose_coupling_decision(event)

    def _highlight_objects(self, objects: Sequence[Dict[str, Any]]):
        gui = self._gui()
        doc_name = self.current_document_name()
        suspended = self._suspend_selection_observer()
        self._applying = True
        try:
            gui.Selection.clearSelection()
            for entry in objects:
                if doc_name and entry.get("object"):
                    gui.Selection.addSelection(doc_name, entry["object"],
                                               ",".join(entry.get("sub_names") or []))
        finally:
            self._applying = False
            if suspended:
                self._resume_selection_observer()

    def _capture(self, category, objects, targets) -> bool:
        """Run the existing capture contract and verify it wrote exactly these targets."""
        if not category:
            return False
        gui = self._gui()
        doc_name = self.current_document_name()
        suspended = self._suspend_selection_observer()
        self._applying = True
        try:
            gui.Selection.clearSelection()
            for entry in objects:
                if doc_name and entry.get("object"):
                    gui.Selection.addSelection(doc_name, entry["object"],
                                               ",".join(entry.get("sub_names") or []))
        finally:
            self._applying = False
        # Start from an empty editor set so a stale captured target can never be
        # mistaken for the result of this confirm.
        setattr(self.panel, category + "_targets", [])
        try:
            self.panel.capture(category)
        finally:
            if suspended:
                self._resume_selection_observer()
        actual = getattr(self.panel, category + "_targets", None)
        if not isinstance(actual, list) or not actual:
            return False
        try:
            expected_keys = sorted((str(entry.get("target_kind")),
                                    int(entry.get("target_id")),
                                    entry.get("side")) for entry in targets)
            actual_keys = sorted((str(entry.get("target_kind")),
                                  int(entry.get("target_id")),
                                  entry.get("side")) for entry in actual)
        except (TypeError, ValueError):
            return False
        return bool(expected_keys) and expected_keys == actual_keys

    def _coupling_row(self, edge_id: int) -> int:
        table = getattr(self.panel, "coupling_table", None)
        if table is None:
            return -1
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and str(item.text()).strip() == str(int(edge_id)):
                return row
        return -1

    def _select_coupling_row(self, edge_id: int, *, mutate_selection: bool = True):
        table = getattr(self.panel, "coupling_table", None)
        row = self._coupling_row(edge_id)
        if table is None or row < 0:
            return
        table.blockSignals(True)
        table.clearSelection()
        table.selectRow(row)
        table.setCurrentCell(row, 0)
        table.blockSignals(False)
        if not mutate_selection:
            # Inside the Selection observer: update the table only, never replace the
            # face the user just picked before the next coupling step.
            return
        name = (self.panel.entities or {}).get((KIND_COUPLING, int(edge_id)))
        gui = self._gui()
        doc_name = self.current_document_name()
        if name and doc_name:
            suspended = self._suspend_selection_observer()
            self._applying = True
            try:
                gui.Selection.clearSelection()
                gui.Selection.addSelection(doc_name, name, "")
            finally:
                self._applying = False
                if suspended:
                    self._resume_selection_observer()

    def _expose_coupling_decision(self, event: Dict[str, Any]):
        table = getattr(self.panel, "coupling_table", None)
        row = self._coupling_row(int(event.get("edge_id", -1)))
        if table is None or row < 0:
            return
        table.blockSignals(True)
        try:
            combo = table.cellWidget(row, 4)
            rotation = event.get("rotation")
            if combo is not None:
                combo.setCurrentIndex(1 if rotation else 0)
            penalty = event.get("penalty")
            item = table.item(row, 5)
            if item is not None and penalty is not None:
                item.setText(str(penalty))
        finally:
            table.blockSignals(False)

    # -- guidance / logging ------------------------------------------------
    def _coupling_records(self) -> List[Dict[str, Any]]:
        project = getattr(self.panel, "project", None) or {}
        records = project.get("couplings", [])
        edges = {edge.get("id"): edge for edge in
                 (getattr(self.panel, "index", {}) or {}).get("edges", [])}
        merged = []
        for record in records:
            item = dict(record)
            edge = edges.get(record.get("edge_id")) or {}
            if "face_ids" not in item and edge.get("face_ids"):
                item["face_ids"] = list(edge["face_ids"])
            merged.append(item)
        return merged

    def _refresh(self):
        setter = getattr(self.panel, "_interaction_set_guidance", None)
        if callable(setter):
            setter(self.machine.render_guidance())
        sync = getattr(self.panel, "_interaction_sync_toggles", None)
        if callable(sync):
            sync()

    def _log(self, message: str):
        logger = getattr(self.panel, "_interaction_log", None)
        if callable(logger):
            logger(message)

    def _refuse(self, error: InteractionRefusal):
        self.machine.last_refusal = error.code
        self.apply(refusal_outcome(error, self._lang()))
