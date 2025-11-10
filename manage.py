from app import create_app
from extensions import db
from flask_migrate import Migrate
import models.models

app = create_app()
migrate = Migrate(app, db)

if __name__ == '__main__':
    app.run()
