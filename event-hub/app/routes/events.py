# app/routes/events.py
# Event browsing pages. UC-001 Discover Events. Backlog #6, #7, #8.
#
# What goes here:
#   Blueprint named 'events'
#   /events       - list upcoming events (#6); search and filter by date, location, category (#8);
#                   show a message when nothing matches
#   /events/<id>  - event details: description, organizer, location, time, price, availability (#7);
#                   cancelled events show their status and hide Register
