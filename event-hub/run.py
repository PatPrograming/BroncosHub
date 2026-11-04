# run.py
# Starts the local website: python run.py -> http://127.0.0.1:5080
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=int(os.environ.get("PORT", "5080")))
