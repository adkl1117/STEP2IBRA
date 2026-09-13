"""Public conversion API; each call owns its state and diagnostic report."""
from __future__ import annotations

import hashlib
import platform
from pathlib import Path
import time

import OCP

from .config import ConversionOptions
from .model import build_cad_json
from .pcurves import PcurveMatcher
from .provenance import annotate_source_faces
from .step_io import parse_step_raw_pcurves, read_step_file


def convert_step(path: Path, options: ConversionOptions | None = None) -> tuple[dict, dict]:
    options = options or ConversionOptions()
    path = Path(path).resolve(strict=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if options.expected_input_sha256 and options.expected_input_sha256 != digest:
        raise ValueError('Profile input SHA256 does not match. Review this model before reusing its face exclusions.')
    started = time.perf_counter()
    shape = read_step_file(path)
    matcher = PcurveMatcher(parse_step_raw_pcurves(path))
    report = {'input': str(path), 'input_sha256': digest, 'options': options.to_dict(),
              'python': platform.python_version(), 'ocp': OCP.__version__}
    data = build_cad_json(shape, matcher, options=options, report=report)
    annotate_source_faces(path, shape, report)
    report['elapsed_seconds'] = round(time.perf_counter()-started, 3)
    return data, report
