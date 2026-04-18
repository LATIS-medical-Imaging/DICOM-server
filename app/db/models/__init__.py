"""ORM models.

Importing this package registers every model with SQLAlchemy's declarative
metadata — this is what Alembic's autogenerate uses to diff the schema.
"""

from app.db.models.annotation import Annotation
from app.db.models.audit_log import AuditLog
from app.db.models.instance import Instance
from app.db.models.patient import Patient
from app.db.models.series import Series
from app.db.models.share import Share
from app.db.models.study import Study
from app.db.models.upload_job import UploadJob
from app.db.models.user import User
from app.db.models.user_session import UserSession

__all__ = [
    "User",
    "UserSession",
    "Patient",
    "Study",
    "Series",
    "Instance",
    "Annotation",
    "Share",
    "UploadJob",
    "AuditLog",
]
