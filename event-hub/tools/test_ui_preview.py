"""Behavior checks for the isolated preview, never the production placeholders.

Run: python -m unittest discover -s tools -p 'test_ui_preview.py' -v
"""

import unittest

from flask import template_rendered

try:
    from .preview_ui import create_preview_app
except ImportError:
    from preview_ui import create_preview_app


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.app = create_preview_app(testing=True)
        self.client = self.app.test_client()

    def role(self, role):
        return self.client.get(f"/preview/role/{role}")

    def state(self):
        with self.client.session_transaction() as session:
            return session["preview"]

    def valid_event(self, **overrides):
        return {"title": "Sample event", "description": "A fictional event for interface testing.",
                "category": "Tech", "location": "Mission Lawn", "start_time": "2026-10-22T18:00",
                "end_time": "2026-10-22T20:00", "price": "0", "capacity": "50", **overrides}

    def test_poster_matches_draft_after_validation_and_publish(self):
        self.role("organizer")
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            invalid = self.client.post("/organizer/events/new", data=self.valid_event(title=""))
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(contexts[-1]["draft_art"], "tech.svg")
        self.assertRegex(invalid.data, br'data-preview-art[^>]*src="/static/images/tech\.svg"')
        published = self.client.post("/organizer/events/new", data=self.valid_event())
        self.assertEqual(published.status_code, 302)
        self.assertEqual(self.state()["events"]["9"]["art"], "tech.svg")
        self.assertIn(b"/static/images/tech.svg", self.client.get(published.location).data)

    def test_existing_poster_survives_other_edits_and_follows_category_change(self):
        self.role("organizer")
        response = self.client.post("/organizer/events/1/edit", data=self.valid_event(category="Arts & Culture", capacity="250"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.state()["events"]["1"]["art"], "sunset.svg")
        changed = self.client.post("/organizer/events/1/edit", data=self.valid_event(category="Wellness", capacity="250"))
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(self.state()["events"]["1"]["art"], "coffee.svg")

    def test_public_and_student_pages_render(self):
        for path in ("/", "/events", "/events/1", "/events/1/register", "/my-registrations",
                     "/my-registrations/101", "/my-registrations/102", "/my-registrations/103",
                     "/registrations/103/checkout", "/login", "/signup", "/profile"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"<main", response.data)
                self.assertIn(b"BroncosHub", response.data)

    def test_organizer_and_admin_pages_render(self):
        self.role("organizer")
        for path in ("/organizer/", "/organizer/events/new", "/organizer/events/1/edit", "/organizer/events/1/stats"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.role("admin")
        self.assertEqual(self.client.get("/admin/reports").status_code, 200)

    def test_role_and_owner_boundaries(self):
        for path in ("/organizer/", "/organizer/events/new", "/organizer/events/1/stats", "/admin/reports"):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.role("organizer")
        self.assertEqual(self.client.get("/organizer/events/2/edit").status_code, 403)
        self.assertEqual(self.client.post("/organizer/events/2/cancel").status_code, 403)
        self.role("guest")
        self.assertEqual(self.client.get("/events/1").status_code, 200)
        self.assertEqual(self.client.post("/events/1/register").status_code, 403)
        self.assertEqual(self.client.get("/my-registrations").status_code, 403)

    def test_free_registration_duplicate_and_cancellation(self):
        response = self.client.post("/events/1/register")
        self.assertEqual(response.status_code, 302)
        detail_url = response.location
        registrations = self.state()["registrations"]
        registration = next(r for r in registrations if r["event_id"] == 1)
        self.assertEqual(registration["status"], "confirmed")
        self.assertEqual(registration["payment_status"], "not_required")
        self.assertEqual(self.client.post("/events/1/register").location, detail_url)
        self.assertEqual(len(self.state()["registrations"]), 4)
        self.assertIn(b"Registration confirmed", self.client.get(detail_url).data)
        self.client.post(detail_url + "/cancel")
        self.assertEqual(self.state()["registrations"][-1]["status"], "cancelled")
        self.assertNotIn(b"DEMO-BH-0104", self.client.get(detail_url).data)

    def test_registration_counts_and_revenue_follow_confirmed_changes(self):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            self.role("organizer")
            self.client.get("/organizer/events/1/stats")
            initial = contexts[-1]["stats"]["registrations"]
            self.client.post("/events/1/register")
            self.client.get("/organizer/events/1/stats")
            self.assertEqual(contexts[-1]["stats"]["registrations"], initial + 1)
            self.client.post("/my-registrations/104/cancel")
            self.client.get("/organizer/events/1/stats")
            self.assertEqual(contexts[-1]["stats"]["registrations"], initial)
            self.client.get("/organizer/events/4/stats")
            self.assertEqual(contexts[-1]["stats"]["revenue_label"], "$150.00")
            self.client.post("/registrations/103/checkout", data={"demo_result": "pending"})
            self.client.get("/organizer/events/4/stats")
            self.assertEqual(contexts[-1]["stats"]["revenue_label"], "$150.00")
            self.client.post("/registrations/103/checkout", data={"demo_result": "succeeded"})
            self.client.get("/organizer/events/4/stats")
            self.assertEqual(contexts[-1]["stats"]["revenue_label"], "$155.00")
            self.client.post("/organizer/events/4/cancel")
            self.client.get("/organizer/events/4/stats")
            self.assertEqual(contexts[-1]["stats"]["registrations"], 0)
            self.assertEqual(contexts[-1]["stats"]["revenue_label"], "$155.00")

    def test_guest_context_contains_no_personal_registration(self):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        self.client.get("/events/2")
        self.role("guest")
        with template_rendered.connected_to(capture, self.app):
            self.client.get("/events/2")
        context = contexts[-1]
        self.assertIsNone(context["user"])
        self.assertIsNone(context["registration"])
        self.assertFalse(context["event"]["registered"])
        self.assertEqual(context["registration_by_event"], {})

    def test_paid_registration_requires_success_before_ticket(self):
        self.client.post("/events/8/register")
        registration = self.state()["registrations"][-1]
        registration_id = registration["id"]
        detail_url = f"/my-registrations/{registration_id}"
        checkout_url = f"/registrations/{registration_id}/checkout"
        self.assertEqual(registration["status"], "pending")
        for result, status in [("pending", "pending"), ("declined", "failed")]:
            self.client.post(checkout_url, data={"demo_result": result})
            self.assertEqual(self.state()["registrations"][-1]["payment_status"], status)
            self.assertNotIn(f"DEMO-BH-{registration_id:04d}".encode(), self.client.get(detail_url).data)
        self.client.post(checkout_url, data={"demo_result": "succeeded"})
        self.assertIn(f"DEMO-BH-{registration_id:04d}".encode(), self.client.get(detail_url).data)
        count = len(self.state()["registrations"])
        self.client.post(checkout_url, data={"demo_result": "succeeded"})
        self.assertEqual(len(self.state()["registrations"]), count)
        self.assertEqual(self.client.post(detail_url + "/cancel").status_code, 409)

    def test_invalid_payment_result_is_rejected(self):
        self.assertEqual(self.client.post("/registrations/103/checkout", data={"demo_result": "arbitrary"}).status_code, 422)
        self.assertEqual(self.state()["registrations"][-1]["payment_status"], "pending")

    def test_event_cancel_invalidates_ticket_and_closes_registration(self):
        self.client.post("/events/1/register")
        self.role("organizer")
        self.client.post("/organizer/events/1/cancel")
        self.assertEqual(self.client.post("/events/1/register").status_code, 409)
        self.assertNotIn(b"DEMO-BH-0104", self.client.get("/my-registrations/104").data)
        self.assertEqual(self.client.get("/organizer/events/1/edit").status_code, 409)

    def test_form_validation_and_escaped_user_content(self):
        self.role("organizer")
        invalid = self.valid_event(title="", end_time="2026-10-22T17:00", price="-1", capacity="0")
        response = self.client.post("/organizer/events/new", data=invalid)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.state()["events"]), 0)
        response = self.client.post("/organizer/events/new", data=self.valid_event(title="<script>alert(1)</script>"))
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(response.location)
        self.assertNotIn(b"<script>alert(1)</script>", detail.data)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", detail.data)

    def test_event_start_cannot_precede_preview_date(self):
        self.role("organizer")
        for start, end in [("2026-10-07T18:00", "2026-10-07T20:00"),
                           ("2026-10-22T18:00+00:00", "2026-10-22T20:00"),
                           ("2026-10-22", "2026-10-22T20:00")]:
            with self.subTest(start=start):
                response = self.client.post("/organizer/events/new", data=self.valid_event(start_time=start, end_time=end))
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.state()["events"], {})
        response = self.client.post("/organizer/events/new", data=self.valid_event(
            start_time="2026-10-08T08:00", end_time="2026-10-08T09:00"))
        self.assertEqual(response.status_code, 302)

    def test_admission_price_is_locked_after_confirmed_or_pending_registration(self):
        self.role("organizer")
        for event_id, attempted_price, original_cents in [(1, "5", 0), (4, "0", 500)]:
            with self.subTest(event=event_id):
                response = self.client.post(f"/organizer/events/{event_id}/edit", data=self.valid_event(
                    price=attempted_price, capacity="250"))
                self.assertEqual(response.status_code, 422)
                self.assertIn(b"admission price is fixed", response.data)
                self.assertNotIn(str(event_id), self.state()["events"])
                self.assertRegex(response.get_data(as_text=True), r'<input id="price"[^>]* readonly')
        # A new paid registration is pending and does not increment attendance,
        # but changing it to free must still be rejected.
        self.client.post("/organizer/events/new", data=self.valid_event(price="5"))
        self.client.post("/events/9/register")
        response = self.client.post("/organizer/events/9/edit", data=self.valid_event(price="0"))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.state()["events"]["9"]["price_cents"], 500)
        self.assertEqual(self.client.get("/registrations/104/checkout").status_code, 200)
        # Other edits remain available when the admission price stays the same.
        response = self.client.post("/organizer/events/9/edit", data=self.valid_event(price="5", title="Updated sample title"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.state()["events"]["9"]["title"], "Updated sample title")

    def test_signup_saves_only_validated_sample_profile(self):
        self.role("guest")
        response = self.client.post("/signup", data={"name": "  Sam Bronco  ", "year_level": "second_year",
            "email": "ignore@example.com", "password": "do-not-save", "confirm_password": "do-not-save", "role": "admin"})
        self.assertEqual(response.location, "/profile")
        self.assertEqual(self.state()["profile"], {"name": "Sam Bronco", "year_level": "second_year"})
        self.assertEqual(self.state()["role"], "student")
        profile = self.client.get("/profile")
        self.assertIn(b'Sam Bronco', profile.data)
        self.assertIn(b'value="second_year" selected', profile.data)
        self.assertNotIn("ignore@example.com", repr(self.state()))
        self.assertNotIn("do-not-save", repr(self.state()))
        # Optional year defaults to unknown; profile uses the same validation.
        self.client.post("/profile", data={"name": "Sam"})
        self.assertEqual(self.state()["profile"], {"name": "Sam", "year_level": "unknown"})

    def test_invalid_signup_preserves_guest_role_and_existing_profile(self):
        self.role("guest")
        for fields in ({"name": " ", "year_level": "first_year"},
                       {"name": "X" * 81, "year_level": "first_year"},
                       {"name": "Sam", "year_level": "invalid-year"}):
            with self.subTest(fields=fields):
                response = self.client.post("/signup", data=fields)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.state()["role"], "guest")
                self.assertEqual(self.state()["profile"], {})

    def test_filtering_and_clear(self):
        result = self.client.get("/events?q=Sunset")
        self.assertIn(b"Sunset Cinema", result.data)
        self.assertNotIn(b"After Hours: Live on Campus", result.data)
        today = self.client.get("/events?date=today")
        self.assertIn(b"Find Your People", today.data)
        self.assertNotIn(b"Sunset Cinema on Mission Lawn", today.data)
        empty = self.client.get("/events?q=no-such-preview-event")
        self.assertIn(b"/events", empty.data)
        all_events = self.client.get("/events")
        self.assertIn(b"Sunset Cinema", all_events.data)
        self.assertIn(b"After Hours: Live on Campus", all_events.data)

    def test_session_isolation_and_reset(self):
        self.client.post("/events/1/register")
        second = self.app.test_client()
        second.get("/")
        with second.session_transaction() as session:
            self.assertEqual(len(session["preview"]["registrations"]), 3)
        self.client.get("/preview/reset", follow_redirects=True)
        self.assertEqual(len(self.state()["registrations"]), 3)

    def test_external_redirects_are_rejected(self):
        for value in ("https://example.com", "//example.com", "/\\example.com"):
            self.assertEqual(self.client.post("/login", data={"next": value}).location, "/")
        self.assertEqual(self.client.post("/login", data={"next": "/events/1"}).location, "/events/1")

    def test_unknown_pages_and_invalid_roles(self):
        for path in ("/missing", "/events/999", "/my-registrations/999", "/preview/role/superuser"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_moderation_removes_sample_event_only_for_admin(self):
        self.assertEqual(self.client.post("/admin/reports/1/remove").status_code, 403)
        self.role("admin")
        self.assertEqual(self.client.post("/admin/reports/1/remove").status_code, 302)
        self.assertEqual(self.state()["events"]["6"]["status"], "cancelled")
        self.assertEqual(self.client.post("/admin/reports/999/remove").status_code, 404)

    def test_passwords_and_payment_fields_are_not_stored(self):
        self.client.post("/login", data={"password": "do-not-save-me", "email": "private@example.com"})
        self.client.post("/signup", data={"password": "do-not-save-me", "confirm_password": "do-not-save-me"})
        self.client.post("/registrations/103/checkout", data={"demo_result": "pending", "card_number": "do-not-save-card"})
        self.assertNotIn("do-not-save", repr(self.state()))
        self.assertNotIn("private@example.com", repr(self.state()))


if __name__ == "__main__":
    unittest.main()
