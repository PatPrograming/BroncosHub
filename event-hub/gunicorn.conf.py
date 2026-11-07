"""Small-campus deployment with conservative process and connection limits."""
import os

bind = os.environ.get("BIND_ADDRESS", "0.0.0.0") + ":" + os.environ.get("PORT", "8000")
workers = 1
threads = 4
worker_class = "gthread"
timeout = 60
accesslog = "-"
errorlog = "-"
# Do not log URL queries (OAuth callback codes and user search text).
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
limit_request_line = 4094
limit_request_fields = 50
limit_request_field_size = 8190
