"""Behavior checks for session-owned, decoded preview cover uploads."""

from io import BytesIO
import unittest
from unittest.mock import patch

from flask import template_rendered
from PIL import Image, PngImagePlugin

try:
    from . import preview_ui as preview
except ImportError:
    import preview_ui as preview


class EventCoverTests(unittest.TestCase):
    def setUp(self):
        self.app = preview.create_preview_app(testing=True)
        self.client = self.app.test_client()
        self.client.get("/preview/role/organizer")

    def fields(self, **extra):
        return {"title": "Cover test", "description": "A sample campus event.",
                "category": "Tech", "location": "Mission Lawn", "start_time": "2026-10-22T18:00",
                "end_time": "2026-10-22T20:00", "price": "0", "capacity": "50", **extra}

    def image(self, format="PNG", color="red", size=(64, 40), metadata=False):
        image = Image.new("RGB", size, color)
        buffer = BytesIO()
        options = {}
        if metadata:
            info = PngImagePlugin.PngInfo()
            info.add_text("Comment", "PRIVATE-METADATA-SAMPLE")
            options["pnginfo"] = info
        image.save(buffer, format=format, **options)
        buffer.seek(0)
        return buffer

    def render(self, path, data=None, client=None):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        client = client or self.client
        with template_rendered.connected_to(capture, self.app):
            response = client.post(path, data=data) if data is not None else client.get(path)
        # Werkzeug's test builder spools large multipart bodies to a temporary
        # input file. The completed request no longer needs that test-only file.
        response.request.environ["wsgi.input"].close()
        return response, contexts[-1] if contexts else None

    def publish(self, image=None, **extra):
        response = self.client.post("/organizer/events/new", data=self.fields(
            cover_image=(image or self.image(), "cover.png"), **extra))
        self.assertEqual(response.status_code, 302)
        _, context = self.render(response.location)
        return context["event"]

    def test_supported_formats_publish_and_retrieve_real_jpeg_pixels(self):
        for format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(format=format):
                event = self.publish(self.image(format=format))
                response = self.client.get(event["cover_url"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/jpeg")
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                with Image.open(BytesIO(response.data)) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.format, "JPEG")
                    self.assertEqual(decoded.size, (64, 40))
                with self.client.session_transaction() as session:
                    saved = session["preview"]["events"][str(event["id"])]
                    self.assertEqual(saved["cover_token"], event["cover_token"])
                    self.assertNotIn("cover_url", saved)
                    self.assertNotIn("data", saved)

    def test_metadata_is_stripped_size_bounded_and_mime_filename_not_trusted(self):
        response = self.client.post("/organizer/events/new", data=self.fields(
            cover_image=(self.image(size=(2400, 1200), metadata=True), "../../not-an-image.exe", "text/plain")))
        self.assertEqual(response.status_code, 302)
        _, context = self.render(response.location)
        event = context["event"]
        self.assertEqual(event["cover_name"], "not-an-image.exe")
        stored = self.client.get(event["cover_url"]).data
        self.assertNotIn(b"PRIVATE-METADATA-SAMPLE", stored)
        with Image.open(BytesIO(stored)) as decoded:
            self.assertEqual(decoded.size, (2000, 1000))
            self.assertFalse(decoded.getexif())
            self.assertNotIn("Comment", decoded.info)

    def test_valid_upload_survives_other_validation_errors_and_retry_without_file(self):
        response, context = self.render("/organizer/events/new", self.fields(
            title="", cover_image=(self.image(), "draft.png")))
        self.assertEqual(response.status_code, 422)
        self.assertIn("title", context["errors"])
        self.assertNotIn("cover_image", context["errors"])
        token, url = context["cover_token"], context["cover_url"]
        self.assertTrue(token)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post("/organizer/events/new", data=self.fields(cover_token=token))
        self.assertEqual(response.status_code, 302)
        _, context = self.render(response.location)
        self.assertEqual(context["event"]["cover_url"], url)

    def test_edit_category_change_replace_and_remove(self):
        event = self.publish()
        edit = f"/organizer/events/{event['id']}/edit"
        _, context = self.render(edit)
        self.assertEqual(context["cover_token"], event["cover_token"])
        response = self.client.post(edit, data=self.fields(category="Sports", title="New title"))
        self.assertEqual(response.status_code, 302)
        _, context = self.render(response.location)
        self.assertEqual(context["event"]["cover_url"], event["cover_url"])
        response = self.client.post(edit, data=self.fields(cover_image=(self.image(color="blue"), "new.png")))
        self.assertEqual(response.status_code, 302)
        _, context = self.render(response.location)
        self.assertNotEqual(context["event"]["cover_token"], event["cover_token"])
        self.assertEqual(context["event"]["cover_name"], "new.png")
        response = self.client.post(edit, data=self.fields(remove_cover="1"))
        self.assertEqual(response.status_code, 302)
        _, context = self.render(response.location)
        self.assertEqual(context["event"]["cover_url"], "")
        self.assertEqual(context["event"]["cover_token"], "")

    def test_invalid_replacement_preserves_saved_or_unsaved_valid_cover(self):
        event = self.publish()
        edit = f"/organizer/events/{event['id']}/edit"
        response, context = self.render(edit, self.fields(remove_cover="1",
            cover_image=(BytesIO(b"<svg onload='evil()'></svg>"), "pretend.jpg", "image/jpeg")))
        self.assertEqual(response.status_code, 422)
        self.assertIn("cover_image", context["errors"])
        self.assertEqual(context["cover_url"], event["cover_url"])
        self.assertEqual(context["form_values"]["remove_cover"], "")
        self.assertEqual(self.client.get(event["cover_url"]).status_code, 200)
        response, draft = self.render("/organizer/events/new", self.fields(title="",
            cover_image=(self.image(), "draft.png")))
        response, context = self.render("/organizer/events/new", self.fields(cover_token=draft["cover_token"],
            cover_image=(BytesIO(b"not pixels"), "bad.png")))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(context["cover_url"], draft["cover_url"])

    def test_remove_choice_survives_validation_retry(self):
        event = self.publish()
        edit = f"/organizer/events/{event['id']}/edit"
        response, context = self.render(edit, self.fields(title="", remove_cover="1"))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(context["cover_token"], "")
        self.assertEqual(context["form_values"]["remove_cover"], "1")
        _, saved = self.render(f"/events/{event['id']}")
        self.assertEqual(saved["event"]["cover_url"], event["cover_url"])
        self.assertEqual(self.client.post(edit, data=self.fields(remove_cover="1")).status_code, 302)
        _, context = self.render(edit)
        self.assertEqual(context["cover_url"], "")

    def test_disallowed_corrupt_oversize_and_excessive_pixel_images_are_rejected(self):
        for raw, filename in [(b"", "empty.png"), (b"not an image", "fake.jpg"),
                              (self.image(format="GIF").getvalue(), "image.gif"),
                              (self.image().getvalue()[:50], "truncated.png"),
                              (b"X" * (preview.MAX_COVER_BYTES + 1), "large.jpg")]:
            with self.subTest(filename=filename):
                response, context = self.render("/organizer/events/new", self.fields(
                    cover_image=(BytesIO(raw), filename)))
                self.assertEqual(response.status_code, 422)
                self.assertIn("cover_image", context["errors"])
                self.assertFalse(context["cover_token"])
        with patch.object(preview, "MAX_COVER_PIXELS", 1000):
            response, context = self.render("/organizer/events/new", self.fields(
                cover_image=(self.image(), "too-many-pixels.png")))
            self.assertEqual(response.status_code, 422)
            self.assertIn("20 megapixels", context["errors"]["cover_image"])
        response = self.client.post("/organizer/events/new", data=self.fields(
            cover_image=(BytesIO(b"X" * (6 * 1024 * 1024)), "request-too-large.jpg")))
        self.assertEqual(response.status_code, 413)
        response.request.environ["wsgi.input"].close()

    def test_cover_reads_and_draft_tokens_are_session_scoped_and_reset_removes_them(self):
        event = self.publish()
        other = self.app.test_client()
        other.get("/preview/role/organizer")
        self.assertEqual(other.get(event["cover_url"]).status_code, 404)
        response, context = self.render("/organizer/events/new", self.fields(
            cover_token=event["cover_token"]), client=other)
        self.assertEqual(response.status_code, 422)
        self.assertIn("cover_image", context["errors"])
        self.assertFalse(context["cover_url"])
        self.client.get("/preview/reset")
        self.assertEqual(self.client.get(event["cover_url"]).status_code, 404)

    def test_upload_requires_organizer_role_and_event_ownership(self):
        self.client.get("/preview/role/student")
        self.assertEqual(self.client.post("/organizer/events/new", data=self.fields(
            cover_image=(self.image(), "image.png"))).status_code, 403)
        self.client.get("/preview/role/organizer")
        self.assertEqual(self.client.post("/organizer/events/2/edit", data=self.fields(
            cover_image=(self.image(), "image.png"))).status_code, 403)


if __name__ == "__main__":
    unittest.main()
