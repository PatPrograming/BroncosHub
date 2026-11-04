"""Fixed-size list pages with bounded offsets and filter-preserving links."""

from flask import abort, request, url_for
from sqlalchemy import func, select

from app.extensions import db


PAGE_SIZE = 24


def paginate(query, *, parameters=None, anchor=None):
    supplied = request.args.getlist("page")
    raw_page = supplied[0] if supplied else "1"
    if (len(supplied) > 1 or not raw_page.isascii() or not raw_page.isdecimal()
            or len(raw_page) > 9 or int(raw_page) < 1):
        abort(400, description="Choose a positive whole page number.")
    page = int(raw_page)
    total = db.session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    # Check the count before constructing a potentially enormous OFFSET.
    if page > pages:
        abort(404, description="That results page no longer exists.")
    items = db.session.scalars(query.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).unique().all()
    filters = {key: value for key, value in (parameters or {}).items() if value}

    def page_url(number):
        return url_for(request.endpoint, **(request.view_args or {}), **filters,
                       page=number, _anchor=anchor)

    return items, {
        "total": total, "page": page, "pages": pages,
        "first": (page - 1) * PAGE_SIZE + 1 if total else 0,
        "last": min(page * PAGE_SIZE, total),
        "previous_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < pages else None,
    }
