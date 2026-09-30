#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Bind the SAP_FIN_DB OAuth connection ID into the pipeline package."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2
PLACEHOLDER = "__BIND_SAP_FIN_DB_OAUTH_CONNECTION_ID__"
SCRIPT_DIR = Path(__file__).resolve().parent
CONTENT_JSON = SCRIPT_DIR / "pipeline-content.json"


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Bind a Fabric OAuth connection ID into the pipeline."
    )
    parser.add_argument("connection_id", help="Fabric connection GUID")
    return parser


def validate_guid(value: str) -> str:
    """Validate and normalize a GUID."""
    return str(uuid.UUID(value))


def replace_placeholder(path: Path, connection_id: str) -> int:
    """Replace all placeholders in one text file."""
    text = path.read_text(encoding="utf-8")
    count = text.count(PLACEHOLDER)
    if count:
        path.write_text(
            text.replace(PLACEHOLDER, connection_id), encoding="utf-8"
        )
    return count


def validate_pipeline(connection_id: str) -> None:
    """Validate the bound pipeline JSON."""
    payload = json.loads(CONTENT_JSON.read_text(encoding="utf-8"))
    properties = payload["properties"]
    serialized = json.dumps(properties)
    if PLACEHOLDER in serialized:
        raise ValueError("One or more connection placeholders remain")
    if serialized.count(connection_id) != 1:
        raise ValueError("Expected the source connection in one location")
    if not re.search(r'"name":\s*"fe_copy_sap_full_snapshot"', serialized):
        raise ValueError("Full-snapshot ForEach activity is missing")

def main() -> int:
    """Bind and validate the native Fabric pipeline definition."""
    try:
        connection_id = validate_guid(create_parser().parse_args().connection_id)
        replacements = replace_placeholder(CONTENT_JSON, connection_id)
        if replacements == 0:
            raise ValueError(
                "No placeholders found. The pipeline might already be bound."
            )
        validate_pipeline(connection_id)
        print(
            json.dumps(
                {
                    "connectionId": connection_id,
                    "replacements": replacements,
                    "pipeline": str(CONTENT_JSON),
                },
                indent=2,
            )
        )
        return EXIT_SUCCESS
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:
        print(f"Unexpected error: {error}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
