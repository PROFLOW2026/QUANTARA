"""Worker process entry point."""

import logging

from quantara_workers.scheduler import WorkerScheduler

logging.basicConfig(level=logging.INFO)


def main() -> None:
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
