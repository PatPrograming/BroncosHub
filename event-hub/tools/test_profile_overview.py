"""Profile summaries must follow registration state rather than sample totals."""

from copy import deepcopy
import unittest

from flask import template_rendered

try:
    from .preview_ui import create_preview_app
    from .ui_fixtures import EVENTS
except ImportError:
    from preview_ui import create_preview_app
    from ui_fixtures import EVENTS


class ProfileOverviewTests(unittest.TestCase):
    def setUp(self):
        self.app = create_preview_app(testing=True)
        self.client = self.app.test_client()

    def overview(self, data=None):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            response = self.client.get("/profile") if data is None else self.client.post("/profile", data=data)
        self.assertEqual(response.status_code, 200 if data is None else 422)
        return contexts[-1]["profile_overview"]

    def test_confirmed_today_and_future_count_and_next_is_chronological(self):
        overview = self.overview()
        self.assertEqual(overview["upcoming_count"], 2)
        self.assertEqual(overview["pending_count"], 1)
        self.assertEqual(overview["next_registration"]["id"], 101)
        self.assertEqual(overview["next_registration"]["event"]["id"], 2)
        self.assertTrue(overview["next_registration"]["ticket_valid"])
        # Newly appended registrations must still be ordered by event date.
        self.client.post("/my-registrations/101/cancel")
        self.client.post("/events/1/register")
        overview = self.overview()
        self.assertEqual(overview["upcoming_count"], 2)
        self.assertEqual(overview["next_registration"]["event"]["id"], 1)

    def test_cancellation_and_payment_results_update_counts(self):
        self.client.post("/my-registrations/101/cancel")
        overview = self.overview()
        self.assertEqual(overview["upcoming_count"], 1)
        self.assertEqual(overview["next_registration"]["id"], 102)
        for payment_result in ("pending", "declined"):
            self.client.post("/registrations/103/checkout", data={"demo_result": payment_result})
            overview = self.overview()
            self.assertEqual((overview["upcoming_count"], overview["pending_count"]), (1, 1))
        self.client.post("/registrations/103/checkout", data={"demo_result": "succeeded"})
        overview = self.overview()
        self.assertEqual((overview["upcoming_count"], overview["pending_count"]), (2, 0))

    def test_cancelled_event_excluded_for_pending_and_confirmed_registration(self):
        self.client.get("/preview/role/organizer")
        self.client.post("/organizer/events/4/cancel")
        overview = self.overview()
        self.assertEqual((overview["upcoming_count"], overview["pending_count"]), (2, 0))
        self.client.get("/preview/reset")
        self.client.post("/registrations/103/checkout", data={"demo_result": "succeeded"})
        self.assertEqual(self.overview()["upcoming_count"], 3)
        self.client.get("/preview/role/organizer")
        self.client.post("/organizer/events/4/cancel")
        self.assertEqual(self.overview()["upcoming_count"], 2)

    def test_confirmed_paid_registration_without_successful_payment_is_not_upcoming(self):
        self.client.get("/profile")
        with self.client.session_transaction() as session:
            for registration in session["preview"]["registrations"]:
                if registration["id"] in (102, 103):
                    registration["status"] = "confirmed"
                    registration["payment_status"] = "failed" if registration["id"] == 102 else "pending"
            session.modified = True
        overview = self.overview()
        self.assertEqual((overview["upcoming_count"], overview["pending_count"]), (1, 0))
        self.assertEqual(overview["next_registration"]["id"], 101)

    def test_past_registrations_are_excluded_even_when_still_confirmed_or_pending(self):
        self.client.get("/profile")
        with self.client.session_transaction() as session:
            for event_id in (2, 4):
                event = deepcopy(next(event for event in EVENTS if event["id"] == event_id))
                event["start_value"] = "2026-10-07T12:00"
                event["end_value"] = "2026-10-07T14:00"
                session["preview"]["events"][str(event_id)] = event
            session.modified = True
        overview = self.overview()
        self.assertEqual((overview["upcoming_count"], overview["pending_count"]), (1, 0))
        self.assertEqual(overview["next_registration"]["id"], 102)

    def test_empty_state_has_no_next_event_and_validation_keeps_summary(self):
        before = self.overview()
        after = self.overview({"name": "", "year_level": "third_year"})
        self.assertEqual(after, before)
        with self.client.session_transaction() as session:
            session["preview"]["registrations"] = []
            session.modified = True
        self.assertEqual(self.overview(), {"upcoming_count": 0, "pending_count": 0, "next_registration": None})

    def test_all_signed_in_preview_roles_share_their_own_state_and_guest_is_blocked(self):
        for role in ("student", "organizer", "admin"):
            self.client.get(f"/preview/role/{role}")
            self.assertEqual(self.overview()["upcoming_count"], 2)
        self.client.get("/preview/role/guest")
        self.assertEqual(self.client.get("/profile").status_code, 403)
        self.assertEqual(self.client.post("/profile", data={"name": "Guest"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
