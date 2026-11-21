"""Validate configuration and prepare Resolve's local runtime directories."""

import argparse
import logging
import os
from pathlib import Path

from .config import Config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        config = Config.load(args.env_file, os.environ)
        logging.basicConfig(
            level=config.log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        config.prepare()
    except (OSError, ValueError) as error:
        logging.getLogger("resolve").error("%s", error)
        return 1

    logging.getLogger("resolve").info("Runtime directories ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
