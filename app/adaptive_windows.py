from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable

from .state_store import read_json, workspace_state_path, write_json

_PROFILE_FILE = "adaptive_windows_profiles.json"
_MAX_APPS = 80
_MAX_HISTORY = 40


class FailureClass(str, Enum):
    app_not_found = "app_not_found"
    empty_accessibility_tree = "empty_accessibility_tree"
    electron_accessibility_locked = "electron_accessibility_locked"
    uia_no_match = "uia_no_match"
    ocr_visible_only = "ocr_visible_only"
    offscreen_or_virtualized = "offscreen_or_virtualized"
    verification_mismatch = "verification_mismatch"
    custom_rendered_surface = "custom_rendered_surface"
    unknown = "unknown"


class SurfaceRuntime(str, Enum):
    window_missing = "window_missing"
    uia_rich = "uia_rich"
    uia_sparse = "uia_sparse"
    electron_locked = "electron_locked"
    visual_text = "visual_text"
    custom_rendered = "custom_rendered"
    unknown = "unknown"


@dataclass
class ResolverStep:
    id: str
    title: str
    reason: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class FailureAnalysis:
    failure_class: FailureClass
    confidence: float
    summary: str
    resolvers: list[ResolverStep]
    learned: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failure_class"] = self.failure_class.value
        return payload


@dataclass
class RuntimePlan:
    runtime: SurfaceRuntime
    confidence: float
    summary: str
    primary_layer: str
    next_tools: list[str]
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runtime"] = self.runtime.value
        return payload


def _profile_path():
    return workspace_state_path(_PROFILE_FILE)


def _app_key(app: str) -> str:
    key = re.sub(r"\s+", " ", (app or "foreground").strip()).lower()
    return key[:120] or "foreground"


def _load_profiles() -> dict[str, Any]:
    data = read_json(_profile_path(), {})
    return data if isinstance(data, dict) else {}


def _save_profiles(data: dict[str, Any]) -> None:
    if len(data) > _MAX_APPS:
        ordered = sorted(
            data.items(),
            key=lambda item: float(item[1].get("updated_at", 0) or 0),
            reverse=True,
        )
        data = dict(ordered[:_MAX_APPS])
    write_json(_profile_path(), data)


def learned_resolvers(app: str, failure_class: str, *, limit: int = 3) -> list[dict[str, Any]]:
    profile = _load_profiles().get(_app_key(app), {})
    history = profile.get("history") or []
    matches = [
        h for h in history
        if h.get("failure_class") == failure_class and h.get("ok") is True
    ]
    matches.sort(key=lambda h: (int(h.get("successes", 0)), float(h.get("ts", 0))), reverse=True)
    return matches[:limit]


def remember_resolver_outcome(
    app: str,
    failure_class: str,
    resolver_id: str,
    ok: bool,
    *,
    detail: str = "",
) -> None:
    key = _app_key(app)
    data = _load_profiles()
    profile = data.setdefault(key, {"app": app or "foreground", "history": []})
    history = profile.setdefault("history", [])
    now = time.time()
    existing = None
    for item in history:
        if (
            item.get("failure_class") == failure_class
            and item.get("resolver_id") == resolver_id
        ):
            existing = item
            break
    if existing is None:
        existing = {
            "failure_class": failure_class,
            "resolver_id": resolver_id,
            "successes": 0,
            "failures": 0,
        }
        history.append(existing)
    if ok:
        existing["successes"] = int(existing.get("successes", 0)) + 1
    else:
        existing["failures"] = int(existing.get("failures", 0)) + 1
    existing["ok"] = bool(ok)
    existing["detail"] = detail[:240]
    existing["ts"] = now
    profile["updated_at"] = now
    profile["history"] = history[-_MAX_HISTORY:]
    _save_profiles(data)


def _quoted_names(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"'([^']{1,80})'", text or ""):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


def _resolver(
    id_: str,
    label: str,
    reason: str,
    tool: str,
    **args: Any,
) -> ResolverStep:
    clean_args = {k: v for k, v in args.items() if v not in ("", None, [])}
    return ResolverStep(id=id_, title=label, reason=reason, tool=tool, args=clean_args)


def _base_resolvers(action: str, query: str, app: str) -> list[ResolverStep]:
    if action == "uia_type":
        return [
            _resolver(
                "ocr_text_target",
                "Find the label by OCR, then paste",
                "The field is not exposed through UIA, but its label may be visible.",
                "uia_type",
                query=query,
                app=app,
            ),
            _resolver(
                "keyboard_focus_path",
                "Use keyboard navigation",
                "Custom surfaces often still support Tab, shortcuts, and paste.",
                "key_combo",
                keys="tab/ctrl+v/enter as appropriate",
            ),
            _resolver(
                "screen_context",
                "Inspect the rendered surface",
                "Use OCR plus screenshot context only after name-based control failed.",
                "screen_context",
            ),
        ]
    if action == "uia_click_sequence":
        return [
            _resolver(
                "split_sequence_at_miss",
                "Re-check the missing target",
                "A sequence should stop at the first unknown control and continue with exact names.",
                "uia_find",
                query=query,
                app=app,
            ),
            _resolver(
                "keyboard_shortcut_path",
                "Use the app keyboard path",
                "Menus, calculators, and games often accept keys more reliably than repeated clicks.",
                "keyboard_type",
                text="shortcut or literal keys for this app",
            ),
            _resolver(
                "screen_context",
                "Inspect the rendered surface",
                "Use visual context only after UIA and OCR names fail.",
                "screen_context",
            ),
        ]
    return [
        _resolver(
            "ocr_text_target",
            "Find visible text with OCR",
            "The target may be painted on screen without being in the accessibility tree.",
            "uia_find",
            query=query,
            app=app,
        ),
        _resolver(
            "keyboard_shortcut_path",
            "Use keyboard navigation or shortcut",
            "If the control is custom-rendered, keyboard focus may still move through it.",
            "key_combo",
            keys="tab/arrow/enter or app shortcut",
        ),
        _resolver(
            "screen_context",
            "Inspect the rendered surface",
            "Escalate to screenshot/OCR context only after text control paths failed.",
            "screen_context",
        ),
    ]


def analyze_windows_failure(
    *,
    action: str,
    query: str = "",
    app: str = "",
    result: dict[str, Any] | None = None,
    output: str = "",
) -> FailureAnalysis:
    result = result or {}
    text = " ".join(
        str(part or "") for part in (
            output,
            result.get("error"),
            result.get("control_reason"),
            result.get("fallback_reason"),
        )
    )
    text_l = text.lower()
    evidence: dict[str, Any] = {
        "action": action,
        "query": query,
        "app": app,
    }
    if result.get("electron_hint"):
        evidence["electron_hint"] = result.get("electron_hint")
    requested = {str(query or "").strip().lower(), str(app or "").strip().lower()}
    names = [
        name for name in _quoted_names(text)
        if name.strip().lower() not in requested
    ]
    if names:
        evidence["available_names"] = names[:24]

    if result.get("electron_hint") or "electron" in text_l and "locked" in text_l:
        failure = FailureClass.electron_accessibility_locked
        summary = "The app looks like an Electron/Chromium shell whose UIA tree is locked."
        confidence = 0.9
        resolvers = [
            _resolver(
                "electron_unlock",
                "Relaunch with renderer accessibility",
                "Chromium apps expose their DOM to UIA after --force-renderer-accessibility.",
                "electron_unlock",
                exe=app or result.get("exe", ""),
            ),
            _resolver(
                "wait_after_unlock",
                "Wait for the target after unlock",
                "The app needs a fresh UIA tree before name-based control works.",
                "uia_wait",
                query=query,
                app=app,
            ),
        ] + _base_resolvers(action, query, app)
    elif "no window titled like" in text_l:
        failure = FailureClass.app_not_found
        summary = "The requested app/window was not found, so the search fell back elsewhere."
        confidence = 0.92
        resolvers = [
            _resolver(
                "wait_for_window",
                "Open or focus the intended window",
                "Do not act in the foreground fallback until the target window is verified.",
                "wait_for_window",
                title=app,
            ),
            _resolver(
                "focus_window",
                "Focus the intended app",
                "A stale or hidden window can make UIA inspect the wrong surface.",
                "focus_window",
                title=app,
            ),
        ]
    elif "no interactive controls" in text_l:
        failure = FailureClass.empty_accessibility_tree
        summary = "The window is present but currently exposes no interactive UIA controls."
        confidence = 0.85
        resolvers = [
            _resolver(
                "focus_and_wait",
                "Focus and wait for controls",
                "The app may be loading, suspended, or exposing controls only after focus.",
                "uia_wait",
                query=query,
                app=app,
            ),
            _resolver(
                "electron_check",
                "Check for Electron accessibility lock",
                "Many Chromium apps hide controls until renderer accessibility is enabled.",
                "electron_check",
                exe=app,
            ),
        ] + _base_resolvers(action, query, app)
    elif "offscreen" in text_l or "virtualized" in text_l:
        failure = FailureClass.offscreen_or_virtualized
        summary = "The target may exist but be offscreen, virtualized, or not scrolled into view."
        confidence = 0.75
        resolvers = [
            _resolver(
                "scroll_then_find",
                "Scroll or expand the region, then find again",
                "Virtualized lists often create controls only when visible.",
                "scroll",
                amount=-5,
            ),
        ] + _base_resolvers(action, query, app)
    elif "could not verify" in text_l or "verify" in text_l and "failed" in text_l:
        failure = FailureClass.verification_mismatch
        summary = "The action may have executed, but the post-action state did not prove success."
        confidence = 0.78
        resolvers = [
            _resolver(
                "read_back_state",
                "Read the target state again",
                "Finish only after the app exposes the expected value or visible state.",
                "uia_find",
                query=query,
                app=app,
            ),
            _resolver(
                "screen_context",
                "Inspect visible state",
                "Use OCR/screenshot context to verify custom-rendered output.",
                "screen_context",
            ),
        ]
    elif names:
        failure = FailureClass.uia_no_match
        summary = "UIA is available, but the requested name does not match this app's actual controls."
        confidence = 0.84
        nearest = names[:3]
        resolvers = [
            _resolver(
                "use_listed_control_name",
                "Use an exact listed control name",
                "The miss response includes real names; copy one exactly instead of guessing.",
                action,
                query=nearest[0] if nearest else query,
                app=app,
            ),
        ] + _base_resolvers(action, query, app)
    elif "no uia control matched" in text_l or "no accessible control" in text_l:
        failure = FailureClass.uia_no_match
        summary = "UIA is available, but the requested control name was not found."
        confidence = 0.7
        resolvers = _base_resolvers(action, query, app)
    elif "screenshot" in text_l or "pixel" in text_l or "canvas" in text_l:
        failure = FailureClass.custom_rendered_surface
        summary = "The target likely lives in a custom-rendered or pixel-only surface."
        confidence = 0.72
        resolvers = [
            _resolver(
                "screen_context",
                "Map the rendered surface",
                "For games/canvas/custom UI, build a visual state map before clicking.",
                "screen_context",
            ),
            _resolver(
                "keyboard_controller_path",
                "Use keyboard/controller semantics",
                "Games and custom canvases usually respond to commands, not named controls.",
                "key_combo",
                keys="arrow/wasd/enter/escape as appropriate",
            ),
        ]
    else:
        failure = FailureClass.unknown
        summary = "The failure did not match a known Windows-control pattern yet."
        confidence = 0.45
        resolvers = _base_resolvers(action, query, app)

    learned = learned_resolvers(app, failure.value)
    if learned:
        learned_steps = [
            _resolver(
                item.get("resolver_id", "learned_resolver"),
                "Reuse learned resolver",
                f"Previously worked for this app {int(item.get('successes', 0))} time(s).",
                item.get("resolver_id", "learned_resolver"),
            )
            for item in learned
        ]
        resolvers = learned_steps + resolvers

    return FailureAnalysis(
        failure_class=failure,
        confidence=confidence,
        summary=summary,
        resolvers=resolvers[:6],
        learned=learned,
        evidence=evidence,
    )


def format_recovery_plan(analysis: FailureAnalysis | dict[str, Any]) -> str:
    if isinstance(analysis, FailureAnalysis):
        payload = analysis.to_dict()
    else:
        payload = analysis
    resolvers = payload.get("resolvers") or []
    parts = []
    for idx, resolver in enumerate(resolvers[:4], start=1):
        title = resolver.get("title") or resolver.get("id") or "resolver"
        tool = resolver.get("tool") or "tool"
        parts.append(f"{idx}. {title} [{tool}]")
    plan = "; ".join(parts) if parts else "1. Re-observe the app state [screen_context]"
    return (
        "Adaptive recovery plan "
        f"({payload.get('failure_class', 'unknown')}, "
        f"{float(payload.get('confidence', 0.0)):.2f}): "
        f"{payload.get('summary', '').strip()} Next: {plan}."
    )


def resolver_ids(steps: Iterable[ResolverStep]) -> list[str]:
    return [step.id for step in steps]


def classify_surface_runtime(
    *,
    app: str = "",
    graph: dict[str, Any] | None = None,
    app_rect: dict[str, Any] | None = None,
    electron_hint: dict[str, Any] | None = None,
    ocr_available: bool = False,
    visual_word_count: int | None = None,
    model_vision: bool = False,
) -> RuntimePlan:
    """Choose the next Windows-control runtime from cheap local evidence."""
    graph = graph or {}
    controls = graph.get("controls") or []
    named = int(graph.get("named_control_count") or len(controls) or 0)
    control_count = int(graph.get("control_count") or 0)
    rect = app_rect or {}
    width = int(rect.get("width") or 0)
    height = int(rect.get("height") or 0)
    window_found = width > 0 and height > 0
    word_count = None if visual_word_count is None else max(0, int(visual_word_count or 0))
    evidence = {
        "app": app or graph.get("app") or "foreground",
        "window_found": window_found,
        "uia_control_count": control_count,
        "named_control_count": named,
        "ocr_available": bool(ocr_available),
        "visual_word_count": word_count,
        "electron_hint": bool(electron_hint),
        "model_vision": bool(model_vision),
    }

    if named >= 12:
        return RuntimePlan(
            runtime=SurfaceRuntime.uia_rich,
            confidence=0.93,
            summary="The app exposes a rich UI Automation tree; use exact UIA names first.",
            primary_layer="uia",
            next_tools=["uia_find", "uia_click", "uia_type", "uia_wait"],
            evidence=evidence,
        )

    if electron_hint:
        return RuntimePlan(
            runtime=SurfaceRuntime.electron_locked,
            confidence=0.88,
            summary="This looks like an Electron/Chromium shell with little or no exposed UIA tree.",
            primary_layer="electron_accessibility",
            next_tools=["electron_check", "electron_unlock", "uia_wait", "adaptive_observe"],
            evidence=evidence,
        )

    if named > 0:
        return RuntimePlan(
            runtime=SurfaceRuntime.uia_sparse,
            confidence=0.78,
            summary="The app exposes some UIA controls, but expect gaps and keep OCR/keyboard fallback ready.",
            primary_layer="uia_then_ocr",
            next_tools=["uia_find", "adaptive_observe", "uia_wait", "screen_context"],
            evidence=evidence,
        )

    if not window_found and app:
        return RuntimePlan(
            runtime=SurfaceRuntime.window_missing,
            confidence=0.9,
            summary="No verified target window bounds yet; resolve or open the app before acting.",
            primary_layer="window_resolution",
            next_tools=["wait_for_window", "focus_window", "adaptive_observe"],
            evidence=evidence,
        )

    if word_count and word_count > 0:
        return RuntimePlan(
            runtime=SurfaceRuntime.visual_text,
            confidence=0.8,
            summary="UIA is empty, but local OCR sees text; use OCR targeting before model vision.",
            primary_layer="ocr",
            next_tools=["uia_find", "screen_context", "key_combo", "mouse_click"],
            evidence=evidence,
        )

    if window_found and word_count == 0:
        tools = ["key_combo", "screen_context", "mouse_click"]
        if model_vision:
            tools.append("computer")
        return RuntimePlan(
            runtime=SurfaceRuntime.custom_rendered,
            confidence=0.82,
            summary="The window exists, but UIA and a bounded OCR probe found no text; treat it like a custom/game surface.",
            primary_layer="keyboard_visual",
            next_tools=tools,
            evidence=evidence,
        )

    if window_found and ocr_available:
        return RuntimePlan(
            runtime=SurfaceRuntime.visual_text,
            confidence=0.66,
            summary="UIA is empty; local OCR is available for visible text targets.",
            primary_layer="ocr",
            next_tools=["uia_find", "screen_context", "key_combo", "mouse_click"],
            evidence=evidence,
        )

    if window_found:
        tools = ["key_combo", "screen_context", "mouse_click"]
        if model_vision:
            tools.append("computer")
        return RuntimePlan(
            runtime=SurfaceRuntime.custom_rendered,
            confidence=0.7,
            summary="The window exists but exposes no accessible controls; treat it like a custom/game surface.",
            primary_layer="keyboard_visual",
            next_tools=tools,
            evidence=evidence,
        )

    return RuntimePlan(
        runtime=SurfaceRuntime.unknown,
        confidence=0.4,
        summary="The available local signals do not identify a reliable Windows-control runtime yet.",
        primary_layer="observe",
        next_tools=["adaptive_observe", "screen_context", "wait_for_window"],
        evidence=evidence,
    )


def format_runtime_plan(plan: RuntimePlan | dict[str, Any]) -> str:
    payload = plan.to_dict() if isinstance(plan, RuntimePlan) else plan
    tools = ", ".join(str(tool) for tool in (payload.get("next_tools") or [])[:5])
    suffix = f" Next: {tools}." if tools else ""
    return (
        f"Runtime plan ({payload.get('runtime', 'unknown')}, "
        f"{float(payload.get('confidence', 0.0)):.2f}): "
        f"{str(payload.get('summary') or '').strip()}{suffix}"
    )


def _affordance_kind(name: str) -> str:
    n = (name or "").strip().lower()
    if not n:
        return "unknown"
    if any(word in n for word in (
        "text", "editor", "message", "search", "file name", "username",
        "password", "email", "input", "field", "address",
    )):
        return "text_input"
    if any(word in n for word in (
        "file", "edit", "view", "tools", "settings", "menu", "more",
        "options", "help",
    )):
        return "menu_or_toolbar"
    if any(word in n for word in (
        "tab", "next", "back", "previous", "home", "server", "channel",
        "page", "list",
    )):
        return "navigation"
    if any(word in n for word in (
        "save", "open", "send", "submit", "ok", "cancel", "delete",
        "apply", "close", "clear", "equals", "plus", "minus", "multiply",
        "divide", "bold", "italic",
    )):
        return "command"
    if re.search(r"\bctrl\+|\balt\+|\bshift\+", n):
        return "command"
    return "control"


def _preferred_actions(kind: str) -> list[str]:
    if kind == "text_input":
        return ["uia_type", "uia_find"]
    if kind == "navigation":
        return ["uia_click", "uia_wait"]
    if kind in {"command", "menu_or_toolbar", "control"}:
        return ["uia_click", "uia_find"]
    return ["uia_find", "screen_context"]


def build_affordance_graph(
    *,
    app: str = "",
    controls: Iterable[str] = (),
    count: int = 0,
    source: str = "uia",
) -> dict[str, Any]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in controls:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)

    affordances = []
    groups: dict[str, list[str]] = {}
    for name in unique:
        kind = _affordance_kind(name)
        groups.setdefault(kind, []).append(name)
        affordances.append({
            "name": name,
            "kind": kind,
            "preferred_actions": _preferred_actions(kind),
        })

    return {
        "app": app or "foreground",
        "source": source,
        "control_count": int(count or len(unique)),
        "named_control_count": len(unique),
        "controls": unique,
        "groups": groups,
        "affordances": affordances,
    }


def format_affordance_graph(graph: dict[str, Any], *, limit: int = 8) -> str:
    app = graph.get("app") or "foreground"
    count = int(graph.get("control_count") or 0)
    named = int(graph.get("named_control_count") or 0)
    groups = graph.get("groups") or {}
    group_bits = []
    for kind in ("text_input", "command", "menu_or_toolbar", "navigation", "control"):
        names = list(groups.get(kind) or [])[:limit]
        if names:
            group_bits.append(f"{kind}: " + ", ".join(repr(n) for n in names))
    if not group_bits:
        group_bits.append("no named affordances exposed")
    return (
        f"Adaptive app map for {app}: {count} UIA nodes, {named} named controls. "
        + " | ".join(group_bits)
    )
