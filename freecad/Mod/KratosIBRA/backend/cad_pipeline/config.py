"""Validated, immutable conversion settings. Model intent belongs in profiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

DEFAULT_TOLERANCE = 1.0e-6
DEFAULT_SAMPLE_COUNT = 17
MIN_CURVE_LENGTH = 1.0e-7


@dataclass(frozen=True)
class ExcludedPlane:
    """Exclude faces entirely within a coordinate-plane slab, not crossing faces."""

    axis: str
    coordinate: float
    tolerance: float = 1.0e-7

    def __post_init__(self):
        if self.axis not in ('x', 'y', 'z'):
            raise ValueError('Plane axis must be x, y or z.')
        if not math.isfinite(self.coordinate) or not math.isfinite(self.tolerance) or self.tolerance <= 0:
            raise ValueError('Plane coordinate must be finite and tolerance positive.')


@dataclass(frozen=True)
class ConversionOptions:
    tolerance: float = DEFAULT_TOLERANCE
    sample_count: int = DEFAULT_SAMPLE_COUNT
    overlap_policy: str = 'merge_duplicates'
    duplicate_relative_area_tolerance: float = 1.0e-4
    overlap_relative_area_tolerance: float = DEFAULT_TOLERANCE
    exclude_face_ids: tuple[int, ...] = ()
    exclude_planes: tuple[ExcludedPlane, ...] = ()
    expected_input_sha256: str | None = None

    def __post_init__(self):
        if not math.isfinite(self.tolerance) or self.tolerance <= 0:
            raise ValueError('tolerance must be finite and positive.')
        if type(self.sample_count) is not int or self.sample_count < 3:
            raise ValueError('sample_count must be an integer >= 3.')
        if self.overlap_policy not in ('merge_duplicates', 'error', 'allow'):
            raise ValueError('overlap_policy must be merge_duplicates, error or allow.')
        for name in ('duplicate_relative_area_tolerance', 'overlap_relative_area_tolerance'):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value < 1:
                raise ValueError(f'{name} must be between 0 and 1.')
        if any(type(i) is not int or i <= 0 for i in self.exclude_face_ids):
            raise ValueError('exclude_face_ids must contain positive integer IDs.')
        if self.expected_input_sha256 is not None:
            digest = self.expected_input_sha256
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('expected_input_sha256 must be a lowercase SHA256 digest.')

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_file(cls, path: Path) -> ConversionOptions:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('Profile must be a JSON object.')
        data['exclude_face_ids'] = tuple(data.get('exclude_face_ids', ()))
        data['exclude_planes'] = tuple(ExcludedPlane(**p) for p in data.get('exclude_planes', ()))
        return cls(**data)  # Unknown keys fail rather than silently changing behavior.
