"""Date-rail integration checks against real server-rendered event pages."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
import unittest

from flask import template_rendered

try:
    from .preview_ui import create_preview_app
except ImportError:
    from preview_ui import create_preview_app


class DateLinks(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "data-campus-date" in attrs:
            self.links.append(attrs)


class CampusWeekTests(unittest.TestCase):
    def setUp(self):
        self.app = create_preview_app(testing=True)
        self.client = self.app.test_client()

    def page(self, query=""):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            response = self.client.get("/events" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True), contexts[-1]

    def test_seven_days_and_native_links_render_before_filter_form(self):
        html, context = self.page()
        days = context["campus_days"]
        self.assertEqual(len(days), 7)
        self.assertEqual(days[0]["iso"], "2026-10-08")
        self.assertEqual(days[-1]["iso"], "2026-10-14")
        links = DateLinks(html).links
        self.assertEqual(len(links), 8)
        self.assertEqual(links[0]["aria-current"], "true")
        self.assertTrue(all(link["href"].startswith("/events") for link in links))
        self.assertLess(html.index('class="campus-week"'), html.index('id="campus-filters"'))
        self.assertIn('id="campus-results"', html)

    def test_exact_day_and_zero_event_day(self):
        html, context = self.page("?date=2026-10-09")
        self.assertEqual([event["id"] for event in context["events"]], [1])
        self.assertEqual(context["date_label"], "Friday, October 9")
        self.assertEqual([day["iso"] for day in context["campus_days"] if day["selected"]], ["2026-10-09"])
        self.assertIn('value="2026-10-09" selected data-exact-date', html)
        self.assertIn('aria-current="date"', html)
        empty_html, empty = self.page("?date=2026-10-12")
        self.assertEqual(empty["events"], [])
        self.assertIn("No plans found. Yet.", empty_html)
        self.assertIn("0 events", empty["result_announcement"])

    def test_rail_counts_ignore_selected_day_but_honor_other_filters(self):
        _, context = self.page("?category=Arts+%26+Culture&date=2026-10-08")
        self.assertEqual(context["events"], [])
        counts = {day["iso"]: day["count"] for day in context["campus_days"]}
        self.assertEqual(counts["2026-10-09"], 1)
        self.assertEqual(counts["2026-10-10"], 1)
        self.assertEqual(counts["2026-10-08"], 0)
        _, context = self.page("?q=Sunset&category=Arts+%26+Culture&location=Mission&date=2026-10-09")
        self.assertEqual(sum(day["count"] for day in context["campus_days"]), 1)
        for day in context["campus_days"]:
            params = parse_qs(urlsplit(day["url"]).query)
            self.assertEqual(params["q"], ["Sunset"])
            self.assertEqual(params["category"], ["Arts & Culture"])
            self.assertEqual(params["location"], ["Mission"])
            self.assertEqual(params["date"], [day["iso"]])

    def test_all_upcoming_clears_only_date(self):
        _, context = self.page("?q=music&category=Arts+%26+Culture&location=Locatelli&date=2026-10-08")
        params = parse_qs(urlsplit(context["clear_date_url"]).query)
        self.assertNotIn("date", params)
        self.assertEqual(params, {"q": ["music"], "category": ["Arts & Culture"], "location": ["Locatelli"]})
        response = self.client.get(context["clear_date_url"])
        self.assertIn(b"After Hours: Live on Campus", response.data)

    def test_existing_relative_filters_still_work(self):
        for value, ids in [("today", {2}), ("week", {1, 2, 3, 7}), ("month", set(range(1, 9))),
                           ("this_week", {1, 2, 3, 7})]:
            with self.subTest(value=value):
                _, context = self.page("?date=" + value)
                self.assertEqual({event["id"] for event in context["events"]}, ids)

    def test_invalid_date_is_normalized_without_a_false_selected_day(self):
        for value in ("2026-02-30", "20261009", "not-a-date"):
            with self.subTest(value=value):
                html, context = self.page("?date=" + value)
                self.assertEqual(context["filters"]["date"], "")
                self.assertEqual(len(context["events"]), 8)
                self.assertFalse(any(day["selected"] for day in context["campus_days"]))
                self.assertEqual(DateLinks(html).links[0]["aria-current"], "true")

    def test_deep_link_opens_the_week_containing_selected_date(self):
        html, context = self.page("?date=2026-10-20")
        self.assertEqual([event["id"] for event in context["events"]], [8])
        self.assertEqual([day["iso"] for day in context["campus_days"] if day["selected"]], ["2026-10-20"])
        self.assertIn('value="2026-10-20" selected data-exact-date', html)

    def test_week_navigation_reaches_later_events_without_changing_results(self):
        _, first = self.page()
        self.assertIsNone(first["calendar_previous"])
        _, second = self.page("?" + urlsplit(first["calendar_next"]).query)
        self.assertEqual(second["campus_days"][0]["iso"], "2026-10-15")
        self.assertEqual(second["campus_days"][-1]["iso"], "2026-10-21")
        self.assertEqual([event["id"] for event in first["events"]], [event["id"] for event in second["events"]])
        later_day = next(day for day in second["campus_days"] if day["iso"] == "2026-10-20")
        self.assertEqual(later_day["count"], 1)
        _, chosen = self.page("?" + urlsplit(later_day["url"]).query)
        self.assertEqual([event["id"] for event in chosen["events"]], [8])
        _, previous = self.page("?" + urlsplit(second["calendar_previous"]).query)
        self.assertEqual(previous["campus_days"], first["campus_days"])

    def test_browsing_keeps_selected_date_and_other_filters(self):
        _, context = self.page("?calendar=2026-10-15&date=2026-10-09&q=Sunset&category=Arts+%26+Culture&location=Mission")
        self.assertEqual([event["id"] for event in context["events"]], [1])
        self.assertFalse(any(day["selected"] for day in context["campus_days"]))
        for key in ("calendar_previous", "calendar_next"):
            params = parse_qs(urlsplit(context[key]).query)
            self.assertEqual(params["date"], ["2026-10-09"])
            self.assertEqual(params["q"], ["Sunset"])
            self.assertEqual(params["category"], ["Arts & Culture"])
            self.assertEqual(params["location"], ["Mission"])
        params = parse_qs(urlsplit(context["clear_date_url"]).query)
        self.assertNotIn("date", params)
        self.assertEqual(params["calendar"], ["2026-10-15"])

    def test_invalid_or_past_calendar_falls_back_and_future_dates_are_bounded(self):
        for value in ("invalid", "2026-02-30", "2020-01-01"):
            _, context = self.page("?calendar=" + value)
            self.assertEqual(context["campus_days"][0]["iso"], "2026-10-08")
            self.assertIsNone(context["calendar_previous"])
        _, context = self.page("?calendar=9999-12-31")
        self.assertEqual(len(context["campus_days"]), 7)
        self.assertIsNone(context["calendar_next"])
        _, context = self.page("?calendar=2026-12-31")
        self.assertIn("2026", context["calendar_range"])
        self.assertIn("2027", context["calendar_range"])

    def test_form_keeps_first_week_when_later_date_remains_selected(self):
        html, context = self.page("?date=2026-10-20&calendar=2026-10-08")
        self.assertEqual(context["calendar_value"], "2026-10-08")
        self.assertIn('name="calendar" value="2026-10-08"', html)
        _, filtered = self.page("?date=2026-10-20&calendar=" + context["calendar_value"] + "&category=Arts+%26+Culture")
        self.assertEqual(filtered["campus_days"][0]["iso"], "2026-10-08")
        self.assertEqual([event["id"] for event in filtered["events"]], [8])

    def test_cancelled_event_disappears_from_day_counts(self):
        self.client.get("/preview/role/organizer")
        self.client.post("/organizer/events/1/cancel")
        _, context = self.page("?date=2026-10-09")
        self.assertEqual(context["events"], [])
        friday = next(day for day in context["campus_days"] if day["iso"] == "2026-10-09")
        self.assertEqual(friday["count"], 0)


if __name__ == "__main__":
    unittest.main()
