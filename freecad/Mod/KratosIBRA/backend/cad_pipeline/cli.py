"""Command-line interface shared by python -m and the original script path."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import tempfile

from .api import convert_step
from .config import ConversionOptions


def write_json(path: Path, data: dict) -> None:
    """Replace a result only after serialization succeeds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=4, allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Convert STEP to CAD JSON with explicit analysis-face selection.')
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--profile', type=Path, help='Model-specific JSON profile; no automatic exclusions by filename.')
    parser.add_argument('--report', type=Path, help='Default: <output>.report.json')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    report_path = args.report or args.output.with_suffix('.report.json')
    paths = [args.input.resolve(), args.output.resolve(), report_path.resolve()]
    if len(set(paths)) != 3:
        parser.error('Input, output and report must be different files.')
    if args.profile and args.profile.resolve() in paths[1:]:
        parser.error('Outputs must not overwrite the profile.')
    options = ConversionOptions.from_file(args.profile) if args.profile else ConversionOptions()
    data, report = convert_step(args.input, options)
    write_json(args.output, data)
    write_json(report_path, report)
    print(f"written: {args.output.resolve()}")
    print(f"faces: {report['input_face_count']} -> {report['output_face_count']}; "
          f"excluded: {len(report['excluded_faces'])}; duplicate faces removed: {len(report['duplicate_faces'])}; "
          f"couplings: {report['coupling_count']}")
    print(f'report: {report_path.resolve()}')


if __name__ == '__main__':
    main()
