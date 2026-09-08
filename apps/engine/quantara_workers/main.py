"""Worker process entry point."""

import logging
import sys

from quantara_workers.scheduler import WorkerScheduler
from quantara_workers.singleton import WorkerAlreadyRunningError, WorkerSingletonLock

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    lock = WorkerSingletonLock()
    try:
        lock.acquire()
    except WorkerAlreadyRunningError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    scheduler = WorkerScheduler()
    scheduler.start()
    try:
        import time

        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
