"""Idempotent schema setup and additive patches.

``Base.metadata.create_all`` creates missing tables but never alters existing
ones, so a deployed volume from an earlier version would be missing
``runs.submit_seq``.  Model modules are imported first so every table is
registered, then each additive patch runs only when its column is really
missing.  The canonical version log lives in ``backend/migrations``.
"""
import logging
from sqlalchemy import inspect,text
from src.config.database import Base,engine as default_engine


def _model_modules():
 from src.models import identity,batch,mapping,run,billing  # noqa: F401


PATCHES=(
 ("runs","submit_seq","ALTER TABLE runs ADD COLUMN IF NOT EXISTS submit_seq INTEGER NOT NULL DEFAULT 0"),
)


def ensure_schema(engine=None):
 engine=engine or default_engine
 _model_modules()
 Base.metadata.create_all(engine)
 inspector=inspect(engine)
 for table,column,statement in PATCHES:
  try:
   existing={c['name'] for c in inspector.get_columns(table)}
   if column in existing:continue
   with engine.begin() as connection:connection.execute(text(statement))
  except Exception:
   # Never block startup on a best-effort patch; the failure is logged and the
   # next boot retries. A fresh database already has every column.
   logging.exception('schema patch failed: %s.%s',table,column)
