"""Five direct tests of User.account_view; no database or Google requests."""

from app.models import User


def make_user(**changes):
    values = {
        "id": 7,
        "name": "Alex Chen",
        "email": "alex@scu.edu",
        "google_sub": "test-google-alex",
        "role": "student",
        "year_level": "third_year",
    }
    values.update(changes)
    return User(**values)


def test_normal_account_view_includes_profile_and_first_name():
    """Normal: a complete profile includes a first-name greeting."""
    # Normal: the account page receives the profile and a first-name greeting.
    view = make_user().account_view()

    assert view == {
        "id": 7,
        "name": "Alex Chen",
        "first_name": "Alex",
        "email": "alex@scu.edu",
        "role": "student",
        "year_level": "third_year",
    }


def test_single_word_name_is_its_own_first_name():
    """Edge: a single-word name is also the first name."""
    # Edge: a name does not need to contain a last name.
    view = make_user(name="Alex").account_view()

    assert view["name"] == "Alex"
    assert view["first_name"] == "Alex"


def test_none_name_uses_bronco_fallback():
    """Invalid: a None name uses the display fallback without changing the user."""
    # Invalid input: the display helper tolerates a missing required name.
    user = make_user(name=None)
    view = user.account_view()

    assert view["name"] == "Bronco"
    assert view["first_name"] == "Bronco"
    assert user.name is None  # Rendering must not change the account itself.


def test_empty_name_uses_bronco_fallback():
    """Invalid: an empty name uses the Bronco display fallback."""
    # Invalid input: an empty name should not produce an empty greeting.
    view = make_user(name="").account_view()

    assert view["name"] == "Bronco"
    assert view["first_name"] == "Bronco"


def test_optional_year_can_be_missing():
    """Edge: a missing optional year leaves the other profile fields intact."""
    # Edge: omitting the optional year must not hide the rest of the profile.
    view = make_user(year_level=None).account_view()

    assert view["year_level"] is None
    assert view["first_name"] == "Alex"
    assert view["email"] == "alex@scu.edu"
