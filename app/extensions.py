"""Flask extension instances initialized via the application factory pattern."""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login_page"
login_manager.login_message = "Please sign in to access this page."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"
