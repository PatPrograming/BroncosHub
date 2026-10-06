# app/services/payment_service.py
# Payment Service. UC-003. Backlog #13, #14, #15.
#
# What goes here:
#   MockPaymentService (fake payments for development, no real money):
#     charge(amount_cents, card_number) -> result with status and reference
#     card 4242... = succeeded, 4000... = declined, 4999... = pending
#   complete_purchase(registration, card_number, service):
#     - save exactly one Payment
#     - succeeded -> registration confirmed + one Ticket
#     - declined/cancelled -> registration failed, no ticket
#     - pending -> registration stays pending, no ticket
