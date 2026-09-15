"""平台配置。

配置来自 YAML（由 vendored PyYAML 解析），覆盖引风机负压目标、电场电压
下限、浆液 pH 联锁阈值、氨站压力下限、排放限值、分析仪默认偏移量和控制台
页面清单。任何缺失或越界取值都在装配阶段抛出 :class:`ConfigError`，不允许
带着半份配置启动控制链。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

import yaml

from .errors import ConfigError

_MISSING = object()


def _section(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    if value is None:
        raise ConfigError(f"配置缺少段落 {key!r}", component="config")
    if not isinstance(value, Mapping):
        raise ConfigError(f"配置段落 {key!r} 必须是映射", component="config")
    return value


def _number(section: Mapping[str, Any], key: str, *, minimum: float | None = None) -> float:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        raise ConfigError(f"配置缺少字段 {key!r}", component="config")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"配置字段 {key!r} 必须是数值", component="config")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ConfigError(
            f"配置字段 {key!r} 不得小于 {minimum}", component="config"
        )
    return number


@dataclass(frozen=True)
class FanLimits:
    """引风机与负压约束。"""

    negative_pressure_target_kpa: float
    negative_pressure_floor_kpa: float
    ramp_rate_kpa_per_s: float
    max_speed_rpm: float
    min_speed_rpm: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "FanLimits":
        limits = cls(
            negative_pressure_target_kpa=_number(section, "negative_pressure_target_kpa"),
            negative_pressure_floor_kpa=_number(section, "negative_pressure_floor_kpa"),
            ramp_rate_kpa_per_s=_number(section, "ramp_rate_kpa_per_s", minimum=0.0),
            max_speed_rpm=_number(section, "max_speed_rpm", minimum=1.0),
            min_speed_rpm=_number(section, "min_speed_rpm", minimum=0.0),
        )
        if limits.negative_pressure_target_kpa > limits.negative_pressure_floor_kpa:
            raise ConfigError(
                "引风机负压目标必须低于（更负于）负压下限", component="config"
            )
        if limits.min_speed_rpm >= limits.max_speed_rpm:
            raise ConfigError("引风机最小转速必须小于最大转速", component="config")
        return limits

@dataclass(frozen=True)
class EspLimits:
    """除尘电场约束。"""

    field_kv_min: float
    field_kv_max: float
    rapping_interval_s: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "EspLimits":
        limits = cls(
            field_kv_min=_number(section, "field_kv_min", minimum=0.0),
            field_kv_max=_number(section, "field_kv_max", minimum=0.0),
            rapping_interval_s=_number(section, "rapping_interval_s", minimum=1.0),
        )
        if limits.field_kv_min > limits.field_kv_max:
            raise ConfigError("电场起晕电压不得高于充电电压上限", component="config")
        return limits


@dataclass(frozen=True)
class ScrubberLimits:
    """浆液 pH 联锁与调节约束。"""

    ph_low: float
    ph_recover: float
    ph_target: float
    circulation_flow_m3_h: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "ScrubberLimits":
        limits = cls(
            ph_low=_number(section, "ph_low"),
            ph_recover=_number(section, "ph_recover"),
            ph_target=_number(section, "ph_target"),
            circulation_flow_m3_h=_number(section, "circulation_flow_m3_h", minimum=1.0),
        )
        if not limits.ph_low < limits.ph_recover <= limits.ph_target:
            raise ConfigError(
                "浆液 pH 必须满足 下限 < 恢复值 <= 目标值", component="config"
            )
        return limits

    def latch_should_set(self, ph: float) -> bool:
        """pH 低于联锁下限时置位闩锁。"""

        return ph < self.ph_low

    def latch_should_reset(self, ph: float) -> bool:
        """pH 回升到恢复值以上时闩锁应当自动复位。"""

        return ph >= self.ph_recover


@dataclass(frozen=True)
class AmmoniaLimits:
    """氨站供氨约束。"""

    supply_pressure_kpa_min: float
    max_rate_kg_h: float
    min_rate_kg_h: float
    safety_margin_kg_h: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "AmmoniaLimits":
        limits = cls(
            supply_pressure_kpa_min=_number(section, "supply_pressure_kpa_min", minimum=0.0),
            max_rate_kg_h=_number(section, "max_rate_kg_h", minimum=1.0),
            min_rate_kg_h=_number(section, "min_rate_kg_h", minimum=0.0),
            safety_margin_kg_h=_number(section, "safety_margin_kg_h", minimum=0.0),
        )
        if limits.min_rate_kg_h >= limits.max_rate_kg_h:
            raise ConfigError("氨量下限必须小于上限", component="config")
        return limits

@dataclass(frozen=True)
class EmissionLimits:
    """烟囱排放限值。"""

    so2_limit_mg_m3: float
    nox_limit_mg_m3: float
    dust_limit_mg_m3: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "EmissionLimits":
        return cls(
            so2_limit_mg_m3=_number(section, "so2_limit_mg_m3", minimum=0.0),
            nox_limit_mg_m3=_number(section, "nox_limit_mg_m3", minimum=0.0),
            dust_limit_mg_m3=_number(section, "dust_limit_mg_m3", minimum=0.0),
        )


@dataclass(frozen=True)
class AnalyzerLimits:
    """烟气分析仪标定约束。"""

    default_offset_mg_m3: float
    max_offset_mg_m3: float
    max_age_s: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "AnalyzerLimits":
        limits = cls(
            default_offset_mg_m3=_number(section, "default_offset_mg_m3"),
            max_offset_mg_m3=_number(section, "max_offset_mg_m3", minimum=0.0),
            max_age_s=_number(section, "max_age_s", minimum=1.0),
        )
        if abs(limits.default_offset_mg_m3) > limits.max_offset_mg_m3:
            raise ConfigError("默认分析仪偏移量超出允许范围", component="config")
        return limits


@dataclass(frozen=True)
class LimeLimits:
    """石灰浆供浆约束。"""

    min_density_pct: float
    max_density_pct: float
    feed_step_pct: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "LimeLimits":
        limits = cls(
            min_density_pct=_number(section, "min_density_pct", minimum=0.0),
            max_density_pct=_number(section, "max_density_pct", minimum=0.0),
            feed_step_pct=_number(section, "feed_step_pct", minimum=0.01),
        )
        if limits.min_density_pct >= limits.max_density_pct:
            raise ConfigError("石灰浆密度下限必须小于上限", component="config")
        return limits


@dataclass(frozen=True)
class PlatformConfig:
    """一份完整的平台配置。"""

    name: str
    project_id: str
    storage_root: str
    fan: FanLimits
    esp: EspLimits
    scrubber: ScrubberLimits
    ammonia: AmmoniaLimits
    emission: EmissionLimits
    analyzer: AnalyzerLimits
    lime: LimeLimits
    pages: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "PlatformConfig":
        meta = _section(mapping, "platform")
        name = meta.get("name")
        project_id = meta.get("project_id")
        storage_root = meta.get("storage_root")
        if not isinstance(name, str) or not name:
            raise ConfigError("platform.name 必须是非空字符串", component="config")
        if not isinstance(project_id, str) or not project_id:
            raise ConfigError("platform.project_id 必须是非空字符串", component="config")
        if not isinstance(storage_root, str) or not storage_root:
            raise ConfigError("platform.storage_root 必须是非空字符串", component="config")

        limits = _section(mapping, "limits")
        pages = mapping.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ConfigError("pages 必须是非空的页面名列表", component="config")
        for page in pages:
            if not isinstance(page, str) or not page.strip():
                raise ConfigError("pages 中的页面名必须是非空字符串", component="config")

        return cls(
            name=name,
            project_id=project_id,
            storage_root=storage_root,
            fan=FanLimits.from_mapping(_section(limits, "fan")),
            esp=EspLimits.from_mapping(_section(limits, "esp")),
            scrubber=ScrubberLimits.from_mapping(_section(limits, "scrubber")),
            ammonia=AmmoniaLimits.from_mapping(_section(limits, "ammonia")),
            emission=EmissionLimits.from_mapping(_section(limits, "emission")),
            analyzer=AnalyzerLimits.from_mapping(_section(limits, "analyzer")),
            lime=LimeLimits.from_mapping(_section(limits, "lime")),
            pages=tuple(pages),
        )


def load_config(path: str) -> PlatformConfig:
    """从 YAML 文件读取配置。"""

    if not os.path.isfile(path):
        raise ConfigError(f"配置文件不存在: {path}", component="config")
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, Mapping):
        raise ConfigError("配置文件顶层必须是映射", component="config")
    return PlatformConfig.from_mapping(raw)


def default_config() -> PlatformConfig:
    """内置默认配置，供未指定配置文件时装配使用。"""

    return PlatformConfig(
        name="FlueGasTreatment",
        project_id="zxy-117-py",
        storage_root="var/state",
        fan=FanLimits(
            negative_pressure_target_kpa=-0.85,
            negative_pressure_floor_kpa=-0.35,
            ramp_rate_kpa_per_s=0.05,
            max_speed_rpm=1480.0,
            min_speed_rpm=320.0,
        ),
        esp=EspLimits(field_kv_min=42.0, field_kv_max=72.0, rapping_interval_s=900.0),
        scrubber=ScrubberLimits(
            ph_low=5.20,
            ph_recover=5.60,
            ph_target=5.80,
            circulation_flow_m3_h=9600.0,
        ),
        ammonia=AmmoniaLimits(
            supply_pressure_kpa_min=0.25,
            max_rate_kg_h=420.0,
            min_rate_kg_h=35.0,
            safety_margin_kg_h=45.0,
        ),
        emission=EmissionLimits(
            so2_limit_mg_m3=35.0, nox_limit_mg_m3=50.0, dust_limit_mg_m3=10.0
        ),
        analyzer=AnalyzerLimits(
            default_offset_mg_m3=0.0, max_offset_mg_m3=25.0, max_age_s=86_400.0
        ),
        lime=LimeLimits(min_density_pct=12.0, max_density_pct=22.0, feed_step_pct=0.8),
        pages=("scrubber", "denox", "esp", "alarms"),
    )


def config_to_mapping(cfg: PlatformConfig) -> dict:
    """把配置还原成可打印／可回写的映射。"""

    return {
        "platform": {
            "name": cfg.name,
            "project_id": cfg.project_id,
            "storage_root": cfg.storage_root,
        },
        "limits": {
            "fan": {
                "negative_pressure_target_kpa": cfg.fan.negative_pressure_target_kpa,
                "negative_pressure_floor_kpa": cfg.fan.negative_pressure_floor_kpa,
                "ramp_rate_kpa_per_s": cfg.fan.ramp_rate_kpa_per_s,
                "max_speed_rpm": cfg.fan.max_speed_rpm,
                "min_speed_rpm": cfg.fan.min_speed_rpm,
            },
            "esp": {
                "field_kv_min": cfg.esp.field_kv_min,
                "field_kv_max": cfg.esp.field_kv_max,
                "rapping_interval_s": cfg.esp.rapping_interval_s,
            },
            "scrubber": {
                "ph_low": cfg.scrubber.ph_low,
                "ph_recover": cfg.scrubber.ph_recover,
                "ph_target": cfg.scrubber.ph_target,
                "circulation_flow_m3_h": cfg.scrubber.circulation_flow_m3_h,
            },
            "ammonia": {
                "supply_pressure_kpa_min": cfg.ammonia.supply_pressure_kpa_min,
                "max_rate_kg_h": cfg.ammonia.max_rate_kg_h,
                "min_rate_kg_h": cfg.ammonia.min_rate_kg_h,
                "safety_margin_kg_h": cfg.ammonia.safety_margin_kg_h,
            },
            "emission": {
                "so2_limit_mg_m3": cfg.emission.so2_limit_mg_m3,
                "nox_limit_mg_m3": cfg.emission.nox_limit_mg_m3,
                "dust_limit_mg_m3": cfg.emission.dust_limit_mg_m3,
            },
            "analyzer": {
                "default_offset_mg_m3": cfg.analyzer.default_offset_mg_m3,
                "max_offset_mg_m3": cfg.analyzer.max_offset_mg_m3,
                "max_age_s": cfg.analyzer.max_age_s,
            },
            "lime": {
                "min_density_pct": cfg.lime.min_density_pct,
                "max_density_pct": cfg.lime.max_density_pct,
                "feed_step_pct": cfg.lime.feed_step_pct,
            },
        },
        "pages": list(cfg.pages),
    }
