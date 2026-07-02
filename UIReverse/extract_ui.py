#!/usr/bin/env python3
"""Extract Unity UI Button positions from serialized RoboMaster client assets."""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import math
import os
import shutil
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import UnityPy
    import yaml
except ImportError as exc:
    print(f"Missing dependency: {exc.name}. Run extract.bat first.", file=sys.stderr)
    raise SystemExit(2) from exc


AUTO = "auto"


def pptr_id(value: Any) -> int:
    if value is None:
        return 0
    for name in ("path_id", "m_PathID"):
        if hasattr(value, name):
            return int(getattr(value, name))
    if isinstance(value, dict):
        return int(value.get("m_PathID", value.get("path_id", 0)))
    return 0


def component_id(entry: Any) -> int:
    if hasattr(entry, "component"):
        return pptr_id(entry.component)
    if isinstance(entry, dict):
        return pptr_id(entry.get("component", entry))
    return pptr_id(entry)


def value_xy(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if value is None:
        return default
    if isinstance(value, dict):
        return float(value.get("x", default[0])), float(value.get("y", default[1]))
    return float(getattr(value, "x", default[0])), float(getattr(value, "y", default[1]))


def value_quaternion(value: Any) -> tuple[float, float, float, float]:
    if value is None:
        return 0.0, 0.0, 0.0, 1.0
    if isinstance(value, dict):
        return tuple(float(value.get(key, fallback)) for key, fallback in zip("xyzw", (0, 0, 0, 1)))  # type: ignore[return-value]
    return tuple(float(getattr(value, key, fallback)) for key, fallback in zip("xyzw", (0, 0, 0, 1)))  # type: ignore[return-value]


def align4(value: int) -> int:
    return (value + 3) & ~3


def normalized_asset_name(value: str) -> str:
    return Path(value.replace("\\", "/")).name.lower()


def read_script_pointer(raw: bytes) -> tuple[int, int] | None:
    # Unity 5+ MonoBehaviour header: GameObject PPtr, enabled byte, padding, MonoScript PPtr.
    if len(raw) < 28:
        return None
    file_id, path_id = struct.unpack_from("<iq", raw, 16)
    if file_id < 0 or path_id < 0:
        return None
    return int(file_id), int(path_id)


def mono_payload_offset(raw: bytes) -> int | None:
    if len(raw) < 32:
        return None
    name_length = struct.unpack_from("<I", raw, 28)[0]
    offset = align4(32 + name_length)
    return offset if offset <= len(raw) else None


@dataclass(frozen=True)
class ScriptInfo:
    name: str
    class_name: str
    namespace: str
    assembly: str


@dataclass(frozen=True)
class CanvasSettings:
    ui_scale_mode: int
    reference_width: float
    reference_height: float
    scale_factor: float
    screen_match_mode: int
    match_width_or_height: float
    fallback_dpi: float
    default_sprite_dpi: float
    physical_unit: int
    source: str


@dataclass(frozen=True)
class Matrix2D:
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def compose(self, child: "Matrix2D") -> "Matrix2D":
        return Matrix2D(
            a=self.a * child.a + self.c * child.b,
            b=self.b * child.a + self.d * child.b,
            c=self.a * child.c + self.c * child.d,
            d=self.b * child.c + self.d * child.d,
            tx=self.a * child.tx + self.c * child.ty + self.tx,
            ty=self.b * child.tx + self.d * child.ty + self.ty,
        )

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty


@dataclass(frozen=True)
class Layout:
    matrix: Matrix2D
    size: tuple[float, float]
    pivot: tuple[float, float]


class ScriptResolver:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._script_files: dict[str, dict[int, ScriptInfo]] = {}

    def _load_script_file(self, path: Path) -> dict[int, ScriptInfo]:
        key = normalized_asset_name(path.name)
        if key in self._script_files:
            return self._script_files[key]
        scripts: dict[int, ScriptInfo] = {}
        if path.is_file():
            env = UnityPy.load(str(path))
            for obj in env.objects:
                if obj.type.name != "MonoScript":
                    continue
                data = obj.read()
                scripts[int(obj.path_id)] = ScriptInfo(
                    name=str(getattr(data, "m_Name", "")),
                    class_name=str(getattr(data, "m_ClassName", "")),
                    namespace=str(getattr(data, "m_Namespace", "")),
                    assembly=str(getattr(data, "m_AssemblyName", "")),
                )
        self._script_files[key] = scripts
        return scripts

    def for_environment(self, asset_path: Path, env: Any) -> Callable[[int, int], ScriptInfo | None]:
        serialized_file = next(iter(env.files.values()))
        local_scripts: dict[int, ScriptInfo] = {}
        for obj in env.objects:
            if obj.type.name != "MonoScript":
                continue
            data = obj.read()
            local_scripts[int(obj.path_id)] = ScriptInfo(
                name=str(getattr(data, "m_Name", "")),
                class_name=str(getattr(data, "m_ClassName", "")),
                namespace=str(getattr(data, "m_Namespace", "")),
                assembly=str(getattr(data, "m_AssemblyName", "")),
            )

        external_maps: list[dict[int, ScriptInfo]] = []
        for external in getattr(serialized_file, "externals", []):
            external_path = self.data_dir / str(getattr(external, "path", ""))
            if not external_path.is_file():
                external_path = self.data_dir / str(getattr(external, "name", ""))
            external_maps.append(self._load_script_file(external_path))

        def resolve(file_id: int, path_id: int) -> ScriptInfo | None:
            if file_id == 0:
                return local_scripts.get(path_id)
            index = file_id - 1
            if 0 <= index < len(external_maps):
                return external_maps[index].get(path_id)
            return None

        return resolve


def parse_canvas_scaler(raw: bytes, source: str) -> CanvasSettings | None:
    offset = mono_payload_offset(raw)
    if offset is None or len(raw) < offset + 40:
        return None
    try:
        ui_scale_mode = struct.unpack_from("<i", raw, offset)[0]
        reference_pixels_per_unit, scale_factor, ref_w, ref_h = struct.unpack_from("<ffff", raw, offset + 4)
        screen_match_mode = struct.unpack_from("<i", raw, offset + 20)[0]
        match = struct.unpack_from("<f", raw, offset + 24)[0]
        physical_unit = struct.unpack_from("<i", raw, offset + 28)[0]
        fallback_dpi, default_sprite_dpi = struct.unpack_from("<ff", raw, offset + 32)
    except struct.error:
        return None
    del reference_pixels_per_unit
    if ui_scale_mode not in (0, 1, 2) or ref_w <= 0 or ref_h <= 0 or scale_factor <= 0:
        return None
    return CanvasSettings(
        ui_scale_mode=ui_scale_mode,
        reference_width=float(ref_w),
        reference_height=float(ref_h),
        scale_factor=float(scale_factor),
        screen_match_mode=int(screen_match_mode),
        match_width_or_height=float(match),
        fallback_dpi=float(fallback_dpi),
        default_sprite_dpi=float(default_sprite_dpi),
        physical_unit=int(physical_unit),
        source=source,
    )


def discover_canvas_settings(scene_path: Path, resolver: ScriptResolver) -> list[CanvasSettings]:
    env = UnityPy.load(str(scene_path))
    resolve_script = resolver.for_environment(scene_path, env)
    results: list[CanvasSettings] = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        raw = obj.get_raw_data()
        pointer = read_script_pointer(raw)
        if not pointer:
            continue
        script = resolve_script(*pointer)
        if not script or script.class_name != "CanvasScaler":
            continue
        parsed = parse_canvas_scaler(raw, f"{scene_path.name}:MonoBehaviour:{obj.path_id}")
        if parsed:
            results.append(parsed)
    return results


def config_number(value: Any, discovered: float, name: str) -> float:
    if isinstance(value, str) and value.lower() == AUTO:
        return discovered
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number or 'auto'") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def config_int(value: Any, discovered: int, name: str) -> int:
    if isinstance(value, str) and value.lower() == AUTO:
        return discovered
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer or 'auto'") from exc


def calculate_canvas_scale(settings: CanvasSettings, width: float, height: float, dpi: float) -> float:
    if settings.ui_scale_mode == 0:
        return settings.scale_factor
    if settings.ui_scale_mode == 1:
        width_scale = width / settings.reference_width
        height_scale = height / settings.reference_height
        if settings.screen_match_mode == 1:  # Expand
            return min(width_scale, height_scale)
        if settings.screen_match_mode == 2:  # Shrink
            return max(width_scale, height_scale)
        match = min(1.0, max(0.0, settings.match_width_or_height))
        return math.exp(math.log(width_scale) * (1.0 - match) + math.log(height_scale) * match)

    effective_dpi = dpi if dpi > 0 else settings.fallback_dpi
    # Unity CanvasScaler.PhysicalUnit: centimeters, millimeters, inches, points, picas.
    units_per_inch = {0: 2.54, 1: 25.4, 2: 1.0, 3: 72.0, 4: 6.0}
    return effective_dpi / units_per_inch.get(settings.physical_unit, 1.0)


def extract_resource_roots(global_managers_path: Path, valid_game_objects: set[int]) -> dict[int, str]:
    env = UnityPy.load(str(global_managers_path))
    roots: dict[int, str] = {}
    for obj in env.objects:
        if obj.type.name != "ResourceManager":
            continue
        tree = obj.read_typetree()
        for entry in tree.get("m_Container", []):
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                continue
            resource_path, pointer = entry
            path_id = pptr_id(pointer)
            if path_id in valid_game_objects:
                roots[path_id] = str(resource_path).replace("\\", "/").lower()
    return roots


def matches_filters(path: str, includes: Iterable[str], excludes: Iterable[str]) -> bool:
    lowered = path.lower()
    included = any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in includes)
    excluded = any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in excludes)
    return included and not excluded


def transform_matrix(position: tuple[float, float], scale: tuple[float, float], rotation: tuple[float, float, float, float]) -> Matrix2D:
    x, y, z, w = rotation
    m00 = 1.0 - 2.0 * (y * y + z * z)
    m01 = 2.0 * (x * y - z * w)
    m10 = 2.0 * (x * y + z * w)
    m11 = 1.0 - 2.0 * (x * x + z * z)
    return Matrix2D(
        a=m00 * scale[0],
        b=m10 * scale[0],
        c=m01 * scale[1],
        d=m11 * scale[1],
        tx=position[0],
        ty=position[1],
    )


def child_layout(transform: Any, transform_type: str, parent: Layout) -> Layout:
    scale = value_xy(getattr(transform, "m_LocalScale", None), (1.0, 1.0))
    rotation = value_quaternion(getattr(transform, "m_LocalRotation", None))
    if transform_type != "RectTransform":
        position = value_xy(getattr(transform, "m_LocalPosition", None), (0.0, 0.0))
        return Layout(parent.matrix.compose(transform_matrix(position, scale, rotation)), (0.0, 0.0), (0.5, 0.5))

    anchor_min = value_xy(getattr(transform, "m_AnchorMin", None), (0.5, 0.5))
    anchor_max = value_xy(getattr(transform, "m_AnchorMax", None), (0.5, 0.5))
    anchored_position = value_xy(getattr(transform, "m_AnchoredPosition", None), (0.0, 0.0))
    size_delta = value_xy(getattr(transform, "m_SizeDelta", None), (0.0, 0.0))
    pivot = value_xy(getattr(transform, "m_Pivot", None), (0.5, 0.5))

    parent_min = (-parent.pivot[0] * parent.size[0], -parent.pivot[1] * parent.size[1])
    anchor_x = anchor_min[0] + (anchor_max[0] - anchor_min[0]) * pivot[0]
    anchor_y = anchor_min[1] + (anchor_max[1] - anchor_min[1]) * pivot[1]
    pivot_position = (
        parent_min[0] + parent.size[0] * anchor_x + anchored_position[0],
        parent_min[1] + parent.size[1] * anchor_y + anchored_position[1],
    )
    size = (
        parent.size[0] * (anchor_max[0] - anchor_min[0]) + size_delta[0],
        parent.size[1] * (anchor_max[1] - anchor_min[1]) + size_delta[1],
    )
    matrix = parent.matrix.compose(transform_matrix(pivot_position, scale, rotation))
    return Layout(matrix, size, pivot)


def local_rect_points(layout: Layout) -> list[tuple[float, float]]:
    left = -layout.pivot[0] * layout.size[0]
    bottom = -layout.pivot[1] * layout.size[1]
    right = left + layout.size[0]
    top = bottom + layout.size[1]
    return [(left, bottom), (right, bottom), (right, top), (left, top)]


def screen_points(layout: Layout, width: float, height: float, scale: float) -> list[tuple[float, float]]:
    result = []
    for x, y in local_rect_points(layout):
        canvas_x, canvas_y = layout.matrix.apply(x, y)
        result.append((width / 2.0 + canvas_x * scale, height / 2.0 - canvas_y * scale))
    return result


def point_summary(points: list[tuple[float, float]]) -> dict[str, Any]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    center_x = sum(xs) / len(xs)
    center_y = sum(ys) / len(ys)
    round_nearest = lambda value: int(math.floor(value + 0.5))
    return {
        "center": {"x": center_x, "y": center_y},
        "center_pixel": {"x": round_nearest(center_x), "y": round_nearest(center_y)},
        "rect": {"x": left, "y": top, "width": right - left, "height": bottom - top},
        "corners": [{"x": x, "y": y} for x, y in points],
    }


def hierarchy_name(transform_id: int, transforms: dict[int, Any], game_objects: dict[int, Any], stop_id: int) -> str:
    names: list[str] = []
    current = transform_id
    seen: set[int] = set()
    while current and current not in seen:
        seen.add(current)
        transform = transforms[current]
        go = game_objects.get(pptr_id(transform.m_GameObject))
        names.append(str(getattr(go, "m_Name", f"<{current}>")))
        if current == stop_id:
            break
        current = pptr_id(getattr(transform, "m_Father", None))
    return "/".join(reversed(names))


def extract_buttons(
    resources_path: Path,
    global_managers_path: Path,
    resolver: ScriptResolver,
    canvas: CanvasSettings,
    screen_width: int,
    screen_height: int,
    dpi: float,
    include_globs: list[str],
    exclude_globs: list[str],
    component_types: set[str],
    include_inactive: bool,
) -> list[dict[str, Any]]:
    env = UnityPy.load(str(resources_path))
    resolve_script = resolver.for_environment(resources_path, env)
    objects = {int(obj.path_id): obj for obj in env.objects}
    game_objects: dict[int, Any] = {}
    transforms: dict[int, Any] = {}
    transform_types: dict[int, str] = {}
    transform_by_go: dict[int, int] = {}
    children: dict[int, list[int]] = {}

    for obj in env.objects:
        path_id = int(obj.path_id)
        if obj.type.name == "GameObject":
            game_objects[path_id] = obj.read()
        elif obj.type.name in ("RectTransform", "Transform"):
            data = obj.read()
            transforms[path_id] = data
            transform_types[path_id] = obj.type.name
            transform_by_go[pptr_id(data.m_GameObject)] = path_id

    for transform_id, transform in transforms.items():
        parent_id = pptr_id(getattr(transform, "m_Father", None))
        children.setdefault(parent_id, []).append(transform_id)

    resource_roots = extract_resource_roots(global_managers_path, set(game_objects))
    selected_roots = {
        transform_by_go[go_id]: path
        for go_id, path in resource_roots.items()
        if go_id in transform_by_go and matches_filters(path, include_globs, exclude_globs)
    }

    button_components: dict[int, list[ScriptInfo]] = {}
    for go_id, game_object in game_objects.items():
        matches: list[ScriptInfo] = []
        for entry in getattr(game_object, "m_Component", []):
            component = objects.get(component_id(entry))
            if not component or component.type.name != "MonoBehaviour":
                continue
            pointer = read_script_pointer(component.get_raw_data())
            script = resolve_script(*pointer) if pointer else None
            if script and (script.class_name in component_types or script.name in component_types):
                matches.append(script)
        if matches:
            button_components[go_id] = matches

    print(
        "Asset scan: "
        f"{len(resource_roots)} resource roots, "
        f"{len(selected_roots)} selected UI roots, "
        f"{len(button_components)} matching component objects."
    )

    screen_scale = calculate_canvas_scale(canvas, screen_width, screen_height, dpi)
    actual_canvas_size = (screen_width / screen_scale, screen_height / screen_scale)
    reference_canvas_size = (canvas.reference_width, canvas.reference_height)
    extracted: list[dict[str, Any]] = []

    for root_transform_id, resource_path in sorted(selected_roots.items(), key=lambda item: item[1]):
        def walk(
            transform_id: int,
            actual_parent: Layout,
            reference_parent: Layout,
            active_parent: bool,
        ) -> None:
            transform = transforms[transform_id]
            actual = child_layout(transform, transform_types[transform_id], actual_parent)
            reference = child_layout(transform, transform_types[transform_id], reference_parent)
            go_id = pptr_id(transform.m_GameObject)
            game_object = game_objects.get(go_id)
            active_self = bool(getattr(game_object, "m_IsActive", True))
            active_in_hierarchy = active_parent and active_self
            scripts = button_components.get(go_id, [])
            if scripts and (include_inactive or active_in_hierarchy):
                screen = point_summary(screen_points(actual, screen_width, screen_height, screen_scale))
                reference_screen = point_summary(
                    screen_points(reference, canvas.reference_width, canvas.reference_height, 1.0)
                )
                extracted.append(
                    {
                        "resource_path": resource_path,
                        "hierarchy": hierarchy_name(transform_id, transforms, game_objects, root_transform_id),
                        "button_name": str(getattr(game_object, "m_Name", "")),
                        "component_types": sorted({script.class_name or script.name for script in scripts}),
                        "component_assemblies": sorted({script.assembly for script in scripts}),
                        "active_self": active_self,
                        "active_in_serialized_hierarchy": active_in_hierarchy,
                        "screen": screen,
                        "reference_canvas": reference_screen,
                    }
                )
            for child_id in children.get(transform_id, []):
                walk(child_id, actual, reference, active_in_hierarchy)

        actual_canvas = Layout(Matrix2D(), actual_canvas_size, (0.5, 0.5))
        reference_canvas = Layout(Matrix2D(), reference_canvas_size, (0.5, 0.5))
        walk(root_transform_id, actual_canvas, reference_canvas, True)

    extracted.sort(key=lambda item: (item["resource_path"], item["hierarchy"]))
    return extracted


def write_csv(path: Path, buttons: list[dict[str, Any]]) -> None:
    fields = [
        "resource_path",
        "hierarchy",
        "button_name",
        "active_self",
        "active_in_serialized_hierarchy",
        "center_x",
        "center_y",
        "rect_x",
        "rect_y",
        "rect_width",
        "rect_height",
        "reference_center_x",
        "reference_center_y",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for button in buttons:
            screen = button["screen"]
            reference = button["reference_canvas"]
            writer.writerow(
                {
                    "resource_path": button["resource_path"],
                    "hierarchy": button["hierarchy"],
                    "button_name": button["button_name"],
                    "active_self": button["active_self"],
                    "active_in_serialized_hierarchy": button["active_in_serialized_hierarchy"],
                    "center_x": screen["center_pixel"]["x"],
                    "center_y": screen["center_pixel"]["y"],
                    "rect_x": screen["rect"]["x"],
                    "rect_y": screen["rect"]["y"],
                    "rect_width": screen["rect"]["width"],
                    "rect_height": screen["rect"]["height"],
                    "reference_center_x": reference["center"]["x"],
                    "reference_center_y": reference["center"]["y"],
                }
            )


def replace_with_retry(source: Path, target: Path, attempts: int = 10) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.2 * (attempt + 1))


def commit_output_pair(
    json_temp: Path,
    csv_temp: Path,
    json_target: Path,
    csv_target: Path,
) -> tuple[Path, Path]:
    try:
        # CSV files are commonly held open by spreadsheet applications, so replace it first.
        replace_with_retry(csv_temp, csv_target)
        replace_with_retry(json_temp, json_target)
        return json_target, csv_target
    except PermissionError:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        fallback_json = json_target.with_name(f"{json_target.stem}_{stamp}{json_target.suffix}")
        fallback_csv = csv_target.with_name(f"{csv_target.stem}_{stamp}{csv_target.suffix}")
        if csv_temp.exists():
            os.replace(csv_temp, fallback_csv)
        else:
            shutil.copyfile(csv_target, fallback_csv)
        if json_temp.exists():
            os.replace(json_temp, fallback_json)
        else:
            shutil.copyfile(json_target, fallback_json)
        print("WARNING: A current output file is open; wrote timestamped output files instead.")
        return fallback_json, fallback_csv


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a mapping")
    return config


def resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="YAML configuration path")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    base = config_path.parent
    paths = config.get("paths", {})
    data_dir = resolve_path(base, str(paths.get("data_dir", "../RoboMasterClient/RoboMasterClient_Data")))
    resources_path = data_dir / str(paths.get("resources_asset", "resources.assets"))
    global_managers_path = data_dir / str(paths.get("global_managers", "globalgamemanagers"))
    scene_path = data_dir / str(paths.get("client_scene", "level1"))
    for required in (resources_path, global_managers_path, scene_path):
        if not required.is_file():
            raise FileNotFoundError(f"Required asset not found: {required}")

    screen_config = config.get("screen", {})
    screen_width = int(screen_config.get("width", 2560))
    screen_height = int(screen_config.get("height", 1440))
    dpi = float(screen_config.get("dpi", 96.0))
    if screen_width <= 0 or screen_height <= 0:
        raise ValueError("screen.width and screen.height must be positive")

    resolver = ScriptResolver(data_dir)
    discovered = discover_canvas_settings(scene_path, resolver)
    if not discovered:
        raise RuntimeError("No CanvasScaler found in the configured client scene")
    preferred = max(discovered, key=lambda item: (item.ui_scale_mode == 1, item.reference_width * item.reference_height))
    canvas_config = config.get("canvas", {})
    canvas = CanvasSettings(
        ui_scale_mode=config_int(canvas_config.get("ui_scale_mode", AUTO), preferred.ui_scale_mode, "canvas.ui_scale_mode"),
        reference_width=config_number(canvas_config.get("reference_width", AUTO), preferred.reference_width, "canvas.reference_width"),
        reference_height=config_number(canvas_config.get("reference_height", AUTO), preferred.reference_height, "canvas.reference_height"),
        scale_factor=config_number(canvas_config.get("scale_factor", AUTO), preferred.scale_factor, "canvas.scale_factor"),
        screen_match_mode=config_int(canvas_config.get("screen_match_mode", AUTO), preferred.screen_match_mode, "canvas.screen_match_mode"),
        match_width_or_height=float(
            preferred.match_width_or_height
            if str(canvas_config.get("match_width_or_height", AUTO)).lower() == AUTO
            else canvas_config["match_width_or_height"]
        ),
        fallback_dpi=config_number(canvas_config.get("fallback_dpi", AUTO), preferred.fallback_dpi, "canvas.fallback_dpi"),
        default_sprite_dpi=config_number(
            canvas_config.get("default_sprite_dpi", AUTO), preferred.default_sprite_dpi, "canvas.default_sprite_dpi"
        ),
        physical_unit=config_int(canvas_config.get("physical_unit", AUTO), preferred.physical_unit, "canvas.physical_unit"),
        source=preferred.source,
    )

    filters = config.get("filters", {})
    include_globs = list(filters.get("include_resource_globs", ["prefabs/ui/**"]))
    exclude_globs = list(filters.get("exclude_resource_globs", []))
    component_types = {str(value) for value in filters.get("component_types", ["Button"])}
    include_inactive = bool(filters.get("include_inactive", True))

    buttons = extract_buttons(
        resources_path=resources_path,
        global_managers_path=global_managers_path,
        resolver=resolver,
        canvas=canvas,
        screen_width=screen_width,
        screen_height=screen_height,
        dpi=dpi,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        component_types=component_types,
        include_inactive=include_inactive,
    )
    if not buttons:
        raise RuntimeError(
            "No matching UI components were extracted. Check filters.component_types "
            "and filters.include_resource_globs in config.yaml."
        )

    output_config = config.get("output", {})
    output_dir = resolve_path(base, str(output_config.get("directory", "output")))
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / str(output_config.get("json_file", "buttons.json"))
    csv_path = output_dir / str(output_config.get("csv_file", "buttons.csv"))
    screen_scale = calculate_canvas_scale(canvas, screen_width, screen_height, dpi)
    document = {
        "metadata": {
            "screen": {"width": screen_width, "height": screen_height, "dpi": dpi},
            "canvas": {
                "ui_scale_mode": canvas.ui_scale_mode,
                "reference_width": canvas.reference_width,
                "reference_height": canvas.reference_height,
                "screen_match_mode": canvas.screen_match_mode,
                "match_width_or_height": canvas.match_width_or_height,
                "calculated_scale": screen_scale,
                "source": canvas.source,
            },
            "filters": {
                "include_resource_globs": include_globs,
                "exclude_resource_globs": exclude_globs,
                "component_types": sorted(component_types),
                "include_inactive": include_inactive,
            },
            "button_count": len(buttons),
            "coordinate_origin": "top-left",
        },
        "buttons": buttons,
    }
    json_temp = json_path.with_name(json_path.name + ".tmp")
    csv_temp = csv_path.with_name(csv_path.name + ".tmp")
    with json_temp.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    write_csv(csv_temp, buttons)
    actual_json_path, actual_csv_path = commit_output_pair(json_temp, csv_temp, json_path, csv_path)

    print(f"Extracted {len(buttons)} UI components for {screen_width}x{screen_height}.")
    print(f"Canvas reference: {canvas.reference_width:g}x{canvas.reference_height:g}, scale={screen_scale:g}")
    print(f"JSON: {actual_json_path}")
    print(f"CSV : {actual_csv_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
