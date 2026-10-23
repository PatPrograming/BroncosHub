"""Lab 5: five direct tests of MockPaymentService, with no payment network."""

from app.services.payment_service import MockPaymentService


def test_successful_mock_card_returns_amount_and_reference():
    """Normal: the success prefix returns the charge amount and a mock reference."""
    result = MockPaymentService().charge(1500, "4242424242424242")

    assert result["status"] == "succeeded"
    assert result["amount_cents"] == 1500
    assert result["reference"].startswith("mock_")
    assert set(result) == {"status", "amount_cents", "reference"}


def test_declined_mock_card_returns_declined():
    """Normal: the decline prefix returns a declined result, not a success."""
    result = MockPaymentService().charge(1500, "4000000000000002")

    assert result["status"] == "declined"
    assert result["amount_cents"] == 1500


def test_pending_mock_card_stays_pending():
    """Normal: the pending prefix leaves the payment unresolved."""
    result = MockPaymentService().charge(1500, "4999000000000003")

    assert result["status"] == "pending"
    assert result["amount_cents"] == 1500


def test_card_with_spaces_and_hyphens_is_normalized():
    """Edge: formatting characters do not change a recognized mock card's result."""
    result = MockPaymentService().charge(1, "4242 4242-4242 4242")

    assert result["status"] == "succeeded"
    assert result["amount_cents"] == 1


def test_unsupported_mock_card_has_no_payment_result():
    """Invalid: an unsupported prefix returns None instead of accepting a payment."""
    result = MockPaymentService().charge(1500, "1234567890123456")

    assert result is None
