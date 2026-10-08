# run.py
# Starts the website for development:  python run.py  ->  http://127.0.0.1:5000
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
