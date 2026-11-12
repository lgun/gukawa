from datetime import datetime
import logging
import signal
from threading import Event, Thread
import time

from filelock import FileLock, Timeout

from .config import load_settings
from .db import Database, utc_now
from .pipeline import process_episode, reconcile
from .source import InstagramSource, SourceError, eligible_episode

log = logging.getLogger(__name__)


def sync(db, settings, *, backfill=False):
    added = 0
    db.set_state("last_sync_attempt", utc_now())
    db.set_state("scan_mode", "backfill" if backfill else "latest")
    db.set_state("scan_added", "0")
    if backfill:
        db.set_state("backfill_pending", "1")
    try:
        for metadata in InstagramSource(settings).discover(backfill=backfill, known_ids=db.known_ids()):
            if not eligible_episode(metadata.get("episode_number"), settings.min_episode):
                continue
            added += db.enqueue(**metadata)
            db.set_state("scan_added", str(added))
        db.set_state("last_sync", utc_now())
        if backfill:
            db.set_state("backfill_complete", utc_now())
            db.set_state("backfill_pending", "")
        db.set_state("sync_error", "")
        db.set_state("scan_mode", "idle")
        log.info("Discovery completed: %s new episode(s), backfill=%s", added, backfill)
        return added
    except SourceError as error:
        db.set_state("sync_error", str(error))
    except Exception as error:
        db.set_state("sync_error", f"새 회차 확인 중 오류가 발생했어요 ({type(error).__name__}).")
    db.set_state("scan_mode", "failed")
    log.warning("Discovery failed; check the admin status")
    return None


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    db = Database(settings.database)
    stopping = Event()

    def stop(*_):
        stopping.set()

    def heartbeat():
        while not stopping.is_set():
            db.set_state("worker_heartbeat", utc_now())
            stopping.wait(15)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
            db.recover()
            reconcile(db, settings)
            thread = Thread(target=heartbeat, daemon=True)
            thread.start()
            last_attempt = db.get_state("last_sync_attempt")
            next_sync = datetime.fromisoformat(last_attempt).timestamp() + settings.sync_interval if last_attempt else 0
            log.info("Worker started; mode=%s, auto-sync=%s", settings.processing_mode, settings.auto_sync)
            try:
                while not stopping.is_set():
                    requested = db.consume_sync_request()
                    due = settings.auto_sync and time.time() >= next_sync
                    if requested or due:
                        backfill = requested == "backfill" or bool(db.get_state("backfill_pending")) or (due and settings.initial_backfill and not db.get_state("backfill_complete"))
                        reconcile(db, settings)
                        sync(db, settings, backfill=backfill)
                        next_sync = time.time() + settings.sync_interval
                    if stopping.is_set():
                        break
                    episode = db.claim()
                    if episode:
                        process_episode(episode, db, settings)
                    else:
                        stopping.wait(2)
            finally:
                stopping.set()
                thread.join(timeout=20)
                db.set_state("worker_heartbeat", "")
    except Timeout:
        raise SystemExit("다른 워커가 실행 중이에요. 워커는 하나만 실행해 주세요.") from None


if __name__ == "__main__":
    run()
