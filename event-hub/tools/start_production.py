"""Run the website and payment recovery together on a single persistent disk."""
from pathlib import Path
import signal
import subprocess
import sys
import time

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))


def main():
    # Initialize schema before two processes attempt to use the same database.
    from app import create_app
    app = create_app()
    if app.config.get("APP_ENV") != "production":
        raise RuntimeError("The production launcher requires APP_ENV=production.")
    children = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    exit_code = 0
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"], cwd=APP_ROOT))
        children.append(subprocess.Popen([sys.executable, "tools/payment_worker.py"], cwd=APP_ROOT))
        while not stopping:
            if any(child.poll() is not None for child in children):
                # Let the hosting service restart BOTH processes. Don't silently
                # keep accepting sales when payment recovery has died.
                exit_code = 1
                break
            time.sleep(1)
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
