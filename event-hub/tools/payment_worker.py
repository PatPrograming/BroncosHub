"""Retry durable payment work in the same database as the web process."""
import logging
from pathlib import Path
import signal
import sys
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.extensions import db
from app.models.checkout_attempt import CheckoutAttempt
from app.observability import report_failure
from app.services.stripe_checkout_service import reconcile_pending
from app.services.notification_service import deliver_pending


def run_cycle(app):
    """Process independent payment and email queues without sharing sessions."""
    if app.config.get("PAYMENT_PROVIDER") == "stripe":
        with app.app_context():
            try:
                reconcile_pending(limit=20)
                failed_refund = db.session.scalar(db.select(CheckoutAttempt.id).where(
                    CheckoutAttempt.status == "refund_pending",
                    CheckoutAttempt.last_error == "Refund failed; operator review required.",
                ).limit(1))
                if failed_refund is not None:
                    report_failure(app, "refund_pending")
            except Exception:
                db.session.rollback()
                app.logger.error("Payment reconciliation failed; retrying next cycle")
                report_failure(app, "payment_recovery")
            finally:
                db.session.remove()
    with app.app_context():
        try:
            result = deliver_pending(limit=20)
            if result.get("failed"):
                app.logger.error("Event notification delivery needs operator review")
                report_failure(app, "notification_failed")
        except Exception:
            db.session.rollback()
            app.logger.error("Event notification worker failed; retrying next cycle")
            report_failure(app, "notification_worker")
        finally:
            db.session.remove()


def main():
    app = create_app()
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        run_cycle(app)
        stop.wait(30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
