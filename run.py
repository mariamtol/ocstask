import os

from taskapp import create_app

app = create_app()

if __name__ == "__main__":
    # On first run, create the database with demo data
    if not os.path.exists(app.config["DATABASE"]):
        from seed import seed
        with app.app_context():
            seed()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
