"""Bound database identifiers before an invalid URL reaches a SQL query."""

from werkzeug.routing import BaseConverter, ValidationError


class DatabaseIdConverter(BaseConverter):
    # The application's primary keys are positive SQLite INTEGER values.
    regex = r"[0-9]{1,19}"
    max_value = 2 ** 63 - 1

    def to_python(self, value):
        number = int(value)
        if not 1 <= number <= self.max_value:
            raise ValidationError()
        return number

    def to_url(self, value):
        try:
            return str(self.to_python(str(value)))
        except (TypeError, ValueError, OverflowError):
            raise ValidationError() from None


def register_id_converter(app):
    """Call before registering blueprints; existing <int:...> routes use it."""
    app.url_map.converters["int"] = DatabaseIdConverter
