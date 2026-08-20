"""Small offline CLI for normalizing OTLP JSON into AgentProof Core traces."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from agentproof_otel.normalize import OtlpFormatError, normalize_json_lines


def _normalize(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        result = normalize_json_lines(path.read_text(encoding="utf-8"))
    except (OSError, OtlpFormatError) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the local-only command-line parser."""

    parser = argparse.ArgumentParser(prog="agentproof-otel")
    subparsers = parser.add_subparsers(dest="command", required=True)
    normalize_parser = subparsers.add_parser(
        "normalize", help="Normalize OTLP JSON or JSON Lines to AgentProof traces."
    )
    normalize_parser.add_argument("path", help="Path to a captured OTLP JSON or JSON Lines file.")
    normalize_parser.set_defaults(handler=_normalize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process-style status code."""

    args = build_parser().parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
