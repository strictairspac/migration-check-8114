from sqlalchemy import Column,Integer,String,Boolean,DateTime,ForeignKey,UniqueConstraint,Index
from src.config.database import Base
class BillingControl(Base):
 """Single row (id=1): feature switch plus the global submission clock.

 ``counter`` increments whenever a migration run is submitted *or* a month is
 closed, both under this row's lock.  That shared clock orders a concurrent
 close against a migration: a run's ``submit_seq`` and a period's
 ``close_seq`` say which request came first.
 """
 __tablename__='billing_control'
 id=Column(Integer,primary_key=True)
 enabled=Column(Boolean,nullable=False,default=True)
 counter=Column(Integer,nullable=False,default=0)
 backfilled=Column(Boolean,nullable=False,default=False)
class BillingPeriod(Base):
 """A closed Beijing-time month and the immutable snapshot taken at close."""
 __tablename__='billing_periods'
 id=Column(Integer,primary_key=True)
 month=Column(String(7),unique=True,nullable=False)
 status=Column(String(20),nullable=False,default='closed')
 close_seq=Column(Integer,nullable=False)
 closed_at=Column(DateTime,nullable=False)
 closed_by=Column(String(120),nullable=False)
 note=Column(String(500),nullable=False,default='')
 closed_count=Column(Integer,nullable=False,default=0)
 closed_checksum=Column(String(64),nullable=False,default='')
class BillingIngestion(Base):
 """First successful target-row persistence per sample_code + test_name.

 Registered even while billing is switched off, so switching the feature on
 later can never retroactively charge an older ingestion, and re-ingesting the
 same natural key stays free.
 """
 __tablename__='billing_ingestions'
 __table_args__=(UniqueConstraint('sample_code','test_name',name='uq_billing_ingestions_key'),)
 id=Column(Integer,primary_key=True)
 sample_code=Column(String(160),nullable=False)
 test_name=Column(String(160),nullable=False)
 run_id=Column(Integer,ForeignKey('runs.id'),nullable=True)
 run_name=Column(String(120),nullable=False,default='')
 batch_name=Column(String(120),nullable=False,default='')
 detection_month=Column(String(7),nullable=False)
 billing_month=Column(String(7),nullable=False)
 source=Column(String(30),nullable=False,default='migration')
 ingested_at=Column(DateTime,nullable=False)
class BillingLine(Base):
 __tablename__='billing_lines'
 __table_args__=(UniqueConstraint('sample_code','test_name',name='uq_billing_lines_key'),Index('ix_billing_lines_month','billing_month'))
 id=Column(Integer,primary_key=True)
 ingestion_id=Column(Integer,ForeignKey('billing_ingestions.id'),nullable=False)
 run_id=Column(Integer,ForeignKey('runs.id'),nullable=False)
 run_name=Column(String(120),nullable=False)
 submit_seq=Column(Integer,nullable=False,default=0)
 batch_name=Column(String(120),nullable=False)
 sample_code=Column(String(160),nullable=False)
 test_name=Column(String(160),nullable=False)
 measured_at=Column(String(40),nullable=False)
 detection_month=Column(String(7),nullable=False)
 billing_month=Column(String(7),nullable=False)
 adjusted=Column(Boolean,nullable=False,default=False)
 late=Column(Boolean,nullable=False,default=False)
 # Row was submitted before the close (keeps its month) but persisted after
 # it, so the month's frozen snapshot needs a review.
 after_close=Column(Boolean,nullable=False,default=False)
 reason=Column(String(200),nullable=False,default='')
 source=Column(String(30),nullable=False,default='migration')
 quantity=Column(Integer,nullable=False,default=1)
 created_at=Column(DateTime,nullable=False)
class BillingSkip(Base):
 """Rows that reached the target table but were deliberately not charged."""
 __tablename__='billing_skips'
 __table_args__=(Index('ix_billing_skips_month','detection_month'),)
 id=Column(Integer,primary_key=True)
 run_id=Column(Integer,ForeignKey('runs.id'),nullable=True)
 run_name=Column(String(120),nullable=False,default='')
 sample_code=Column(String(160),nullable=True)
 test_name=Column(String(160),nullable=True)
 detection_month=Column(String(7),nullable=True)
 reason=Column(String(60),nullable=False)
 detail=Column(String(500),nullable=False,default='')
 created_at=Column(DateTime,nullable=False)
