"""Run the Resolve posting and optional Telegram review services."""

import argparse
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from time import sleep
from urllib.parse import urlsplit

from .bundles import receive_bundles
from .config import read_env_file
from .control import Pipeline
from .posting import MetaPublisher
from .reels import configured_builder
from .scheduler import Scheduler
from .telegram import ReviewBot, TelegramAPI


def build_services(pipeline, settings, *, telegram=False, live=False, media_base="", zone="UTC"):
    """Assemble the operational adapters with an explicit live opt-in."""
    configured_dry_run = settings.get("META_DRY_RUN", "true").casefold()
    if configured_dry_run not in {"true", "false"}:
        raise ValueError("META_DRY_RUN must be true or false")
    if live and configured_dry_run != "false":
        raise ValueError("Live mode requires --live and META_DRY_RUN=false")
    if live and media_base:
        parsed = urlsplit(media_base)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("Live mode requires a public HTTPS media base URL")

    bot = None
    alert = None
    if telegram:
        api = TelegramAPI(settings.get("TELEGRAM_BOT_TOKEN", ""))
        try:
            chat_id = int(settings["TELEGRAM_CHAT_ID"])
            reviewer = int(settings["TELEGRAM_REVIEWER_ID"])
        except (KeyError, ValueError) as error:
            raise ValueError("Set numeric Telegram chat and reviewer IDs") from error
        bot = ReviewBot(api, pipeline, chat_id=chat_id, reviewer_id=reviewer)
        alert = lambda summary: api.failure_alert(chat_id, summary)

    version = settings.get("META_GRAPH_VERSION", "") or ("v24.0" if not live else "")
    publishers = {
        platform: MetaPublisher(platform, dry_run=not live, version=version,
                                account_id=settings.get(key, ""),
                                token=settings.get("META_ACCESS_TOKEN", ""))
        for platform, key in (("instagram", "META_IG_USER_ID"), ("facebook", "META_PAGE_ID"))
    }

    def media_url(reel_id):
        if live and not media_base:
            return pipeline.directory / f"{reel_id}.mp4"
        return f"{media_base.rstrip('/')}/{reel_id}.mp4" if live else ""

    return Scheduler(pipeline, publishers, media_url, zone=zone, alert=alert), bot


def run_iteration(scheduler, bot, *, offset, now, logger):
    if bot is not None:
        try:
            bot.send_next_preview()
        except (OSError, RuntimeError, ValueError) as error:
            logger.warning("Telegram preview could not be sent (%s)", type(error).__name__)
        try:
            offset = bot.poll(offset)
        except (OSError, RuntimeError, ValueError) as error:
            logger.warning("Telegram poll failed (%s)", type(error).__name__)
    for slot, platform, status in scheduler.tick(now):
        logger.info("%s %s %s", slot, platform, status)
    return offset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("state/dashboard"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--zone", default="UTC")
    parser.add_argument("--media-base", default="")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = read_env_file(args.env_file) | dict(os.environ)
    try:
        pipeline = Pipeline(args.state, args.source, reel_builder=configured_builder(settings))
        scheduler, bot = build_services(pipeline, settings, telegram=args.telegram,
                                        live=args.live, media_base=args.media_base, zone=args.zone)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("resolve.operator")
    offset = 0
    inbox = settings.get("RESOLVE_BUNDLE_INBOX")
    if inbox:
        Path(inbox).mkdir(parents=True, exist_ok=True)
    if pipeline.reel_builder and inbox:
        pipeline.start()
    try:
        while True:
            if inbox:
                try:
                    now = datetime.now(timezone.utc)
                    receive_bundles(pipeline, Path(inbox), now, scheduler.alert)
                    if pipeline.reel_builder and pipeline.running:
                        pipeline.enqueue(int(pipeline.settings()["volume"]), production_only=True,
                                         production_day=now.astimezone(scheduler.zone).date().isoformat())
                except (OSError, RuntimeError, ValueError) as error:
                    logger.warning("Bundle monitoring: %s", type(error).__name__)
            offset = run_iteration(scheduler, bot, offset=offset,
                                   now=datetime.now(timezone.utc), logger=logger)
            if args.once:
                return 0
            sleep(5 if bot else 30)
    except KeyboardInterrupt:
        return 0
    finally:
        if pipeline.reel_builder and inbox:
            pipeline.stop()


if __name__ == "__main__":
    raise SystemExit(main())
