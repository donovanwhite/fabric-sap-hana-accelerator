"""Command-line entry point for the SAP Finance Fabric deployer."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .deployer import AcceleratorDeployer, DeploymentOptions

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_CONFIGURATION = 2


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Deploy the SAP Finance accelerator into an existing Fabric workspace"
        )
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Existing Fabric workspace display name or ID",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Update compatible items when names already exist",
    )
    parser.add_argument(
        "--sql-connection",
        default="conn_sap_finance_sql",
        help=(
            "Authorized ShareableCloud FabricSql OAuth2 connection name or ID "
            "(default: conn_sap_finance_sql)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate prerequisites and print the deployment plan",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def configure_logging(verbose: bool) -> None:
    """Configure concise console logging."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def repository_root() -> Path:
    """Resolve the repository root from the installed source location."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "artefacts").is_dir() and (parent / "deployment").is_dir():
            return parent
    working = Path.cwd()
    if (working / "artefacts").is_dir() and (working / "deployment").is_dir():
        return working
    raise FileNotFoundError(
        "Repository root was not found. Run from the cloned accelerator repository."
    )


def main() -> int:
    """Deploy the accelerator and return a standard exit code."""
    args = create_parser().parse_args()
    configure_logging(args.verbose)
    try:
        deployer = AcceleratorDeployer(
            repository_root(),
            DeploymentOptions(
                workspace=args.workspace,
                sql_connection=args.sql_connection,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            ),
        )
        items = deployer.deploy()
        if args.dry_run:
            logging.info("Dry run passed; no Fabric items were changed")
        else:
            logging.info("Deployment succeeded with %d resolved items", len(items))
            logging.info(
                "Next step: run pl_full_medallion_sap_finance in Fabric"
            )
        return EXIT_SUCCESS
    except (FileNotFoundError, ValueError) as error:
        logging.error("%s", error)
        return EXIT_CONFIGURATION
    except Exception:
        logging.exception("Deployment failed")
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
