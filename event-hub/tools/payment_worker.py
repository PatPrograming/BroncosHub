"""Retry durable payment work on the same disk as the web process."""
import logging
from pathlib import Path
import signal
import sys
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.extensions import db
from app.services.stripe_checkout_service import reconcile_pending


def main():
    app = create_app()
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        if app.config.get("PAYMENT_PROVIDER") == "stripe":
            with app.app_context():
                try:
                    reconcile_pending(limit=20)
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Payment reconciliation failed; retrying next cycle")
                finally:
                    db.session.remove()
        stop.wait(30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
