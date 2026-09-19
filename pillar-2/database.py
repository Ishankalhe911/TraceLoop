import os
import uuid
import enum
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, String, Boolean, DateTime, Enum, 
    ForeignKey, BigInteger, Integer, JSON, Numeric, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker
load_dotenv()
# --- DATABASE CONFIGURATION ---
# Default to SQLite for immediate zero-setup testing. 
# To upgrade to PostgreSQL for production, simply change the .env variable to:
# DATABASE_URL=postgresql://user:password@localhost:5432/traceloop
# Using sqlalchemy.types.JSON automatically maps to JSONB in Postgres, 
# but safely degrades to TEXT in SQLite, preventing crashes during testing[cite: 15].
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./traceloop.db")

# SQLite requires specific threading arguments
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(
    DATABASE_URL, 
    connect_args=connect_args,
    pool_pre_ping=True,  # Sends a silent "Hello?" before executing the real query
    pool_recycle=300     # Proactively refreshes the connection every 5 minutes
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 1. ENUMS (Trace-Loop State Machines) ---
class UserRole(str, enum.Enum):
    FIRST_BUYER = "FIRST_BUYER"
    BUYER = "BUYER"
    RESELLER = "RESELLER"
    VERIFIABLE = "VERIFIABLE"
    RECYCLER = "RECYCLER"
    ADMIN = "ADMIN"

class UserStatus(str, enum.Enum):
    PENDING = "PENDING"
    KYC_IN_PROGRESS = "KYC_IN_PROGRESS"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REJECTED = "REJECTED"

class DocType(str, enum.Enum):
    PURCHASE_PROOF = "PURCHASE_PROOF"
    BUSINESS_PROOF = "BUSINESS_PROOF"
    BRAND_AUTH = "BRAND_AUTH"
    CPCB_CERT = "CPCB_CERT"

class DocStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"

class DeviceStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"
    VERIFIED = "VERIFIED"
    TRANSFERRED = "TRANSFERRED"
    DISPUTED = "DISPUTED"
    RECYCLED = "RECYCLED"
    EXPORTED = "EXPORTED"

class ChangeType(str, enum.Enum):
    REPLACED = "REPLACED"
    UPGRADED = "UPGRADED"
    REMOVED = "REMOVED"
    ADDED = "ADDED"

class ChainStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"

class VerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    STAMPED = "STAMPED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

class TransferStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

class DisputeType(str, enum.Enum):
    MISREPRESENTED_SPEC = "MISREPRESENTED_SPEC"
    STOLEN = "STOLEN"
    FAKE_STAMP = "FAKE_STAMP"
    OWNERSHIP_DISPUTE = "OWNERSHIP_DISPUTE"
    OTHER = "OTHER"

class DisputeStatus(str, enum.Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"

class AdminActionType(str, enum.Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SUSPEND = "SUSPEND"
    FLAG_CLEAR = "FLAG_CLEAR"
    REVOKE = "REVOKE"
    DOC_REVIEW = "DOC_REVIEW"

class RecyclingStatus(str, enum.Enum):
    SENT = "SENT"
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    RECYCLED = "RECYCLED"


# --- 2. AUTH & IDENTITY MODELS (Pillar 2) ---
class User(Base):
    """Matches Section 1.3 Users Table[cite: 15]"""
    __tablename__ = "users"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone = Column(String(15), unique=True, nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    status = Column(Enum(UserStatus), default=UserStatus.PENDING, nullable=False)
    name = Column(String(120), nullable=True)
    
    # DPDPA Compliance: Only store the last 4 digits off-chain[cite: 14, 15]
    aadhaar_last4 = Column(String(4), nullable=True) 
    aadhaar_verified = Column(Boolean, default=False)
    
    gst_number = Column(String(20), nullable=True)
    cpcb_number = Column(String(40), nullable=True)
    brand_auth_code = Column(String(60), nullable=True)
    service_center_id = Column(String(60), nullable=True)
    
    admin_approved = Column(Boolean, default=False)
    admin_approved_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    admin_approved_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    reward_points = Column(Integer, default=0)   # Running total — shown in dashboard badge
    dispute_count = Column(Integer, default=0)   # Disputed stamps — verifier oversight

class KYCDocument(Base):
    __tablename__ = "kyc_documents"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    doc_type = Column(Enum(DocType), nullable=False)
    file_path = Column(Text, nullable=False)
    file_hash = Column(String(64), nullable=False)
    status = Column(Enum(DocStatus), default=DocStatus.PENDING, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    reviewed_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    serial_for_device = Column(String(80), nullable=True) 
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class OTPSession(Base):
    __tablename__ = "otp_sessions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone = Column(String(15), nullable=False)
    otp_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class JWTSession(Base):
    __tablename__ = "sessions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    refresh_token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


# --- 3. DEVICE INTELLIGENCE MODELS (Pillar 3) ---
class Device(Base):
    """Matches Section 2.2 Devices Table"""
    __tablename__ = "devices"

    id = Column(String(40), primary_key=True, index=True) 
    serial_hash = Column(String(64), unique=True, nullable=False)
    serial_raw = Column(String(80), nullable=False)
    brand_code = Column(String(4), nullable=False)
    brand_name = Column(String(40), nullable=False)
    
    original_config = Column(JSON, nullable=False)
    current_config = Column(JSON, nullable=False)
    
    current_owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    current_owner_hash = Column(String(64), nullable=False)
    
    status = Column(Enum(DeviceStatus), default=DeviceStatus.REGISTERED, nullable=False)
    stamp_valid = Column(Boolean, default=False)
    
    # --- NEW MARKETPLACE FIELDS ---
    is_for_sale = Column(Boolean, default=False)
    asking_price = Column(Numeric(10, 2), nullable=True)
    city = Column(String(60), nullable=True)
    
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_verified_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    
    registered_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
class DeviceHardwareLog(Base):
    __tablename__ = "device_hardware_log"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(40), ForeignKey("devices.id"), nullable=False)
    component = Column(String(80), nullable=False)
    change_type = Column(Enum(ChangeType), nullable=False)
    old_spec = Column(Text, nullable=True)
    new_spec = Column(Text, nullable=False)
    declared_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    stamp_invalidated = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class ManufacturerCache(Base):
    __tablename__ = "manufacturer_cache"
    
    serial_hash = Column(String(64), primary_key=True)
    brand_code = Column(String(4), nullable=False)
    original_config = Column(JSON, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    source = Column(String(30), nullable=False)


# --- 4. LEDGER MIRROR (Pillar 1) ---
class LedgerEvent(Base):
    """Matches Section 3.2 Ledger Events. Populated by Subscriber Daemon[cite: 15]."""
    __tablename__ = "ledger_events"

    id = Column(String(36), primary_key=True) # Maps to on-chain UUID
    device_id = Column(String(40), ForeignKey("devices.id"), nullable=False)
    event_type = Column(String(20), nullable=False)
    actor_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    actor_type = Column(String(20), nullable=False)
    
    tx_hash = Column(String(80), nullable=True, index=True)
    block_number = Column(BigInteger, nullable=True)
    chain_status = Column(Enum(ChainStatus), default=ChainStatus.PENDING, nullable=False)
    
    metadata_ = Column("metadata", JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


# --- 5. WORKFLOW MODELS (Verification, Transfer, Dispute) ---
class VerificationRequest(Base):
    """Matches Section 4.3 Verifications[cite: 15]"""
    __tablename__ = "verifications"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(40), ForeignKey("devices.id"), nullable=False)
    requested_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    verifier_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    
    status = Column(Enum(VerificationStatus), default=VerificationStatus.PENDING, nullable=False)
    config_snapshot = Column(JSON, nullable=True)
    manufacturer_match = Column(Boolean, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    stamped_at = Column(DateTime(timezone=True), nullable=True)
    ledger_event_id = Column(String(36), ForeignKey("ledger_events.id"), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

class VerifierReputation(Base):
    __tablename__ = "verifier_reputation"
    
    verifier_id = Column(String(36), ForeignKey("users.id"), primary_key=True)
    total_stamps = Column(Integer, default=0)
    disputed_stamps = Column(Integer, default=0)
    rejected_stamps = Column(Integer, default=0)
    reputation_score = Column(Numeric(4, 2), default=100.00)
    flagged = Column(Boolean, default=False)
    last_calculated_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class Transfer(Base):
    """Matches Section 5.4 Transfers[cite: 15]"""
    __tablename__ = "transfers"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(40), ForeignKey("devices.id"), nullable=False)
    from_user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    to_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    verifier_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    
    status = Column(Enum(TransferStatus), default=TransferStatus.PENDING, nullable=False)
    
    stamp_event_id = Column(String(36), ForeignKey("ledger_events.id"), nullable=True)
    transfer_event_id = Column(String(36), ForeignKey("ledger_events.id"), nullable=True)
    
    velocity_flagged = Column(Boolean, default=False)
    count_flagged = Column(Boolean, default=False)
    
    initiated_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)

class Dispute(Base):
    """Matches Section 6.2 Disputes[cite: 15]"""
    __tablename__ = "disputes"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(40), ForeignKey("devices.id"), nullable=False)
    raised_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    raised_by_role = Column(Enum(UserRole), nullable=False)
    
    dispute_type = Column(Enum(DisputeType), nullable=False)
    description = Column(Text, nullable=False)
    evidence_path = Column(Text, nullable=True)
    
    status = Column(Enum(DisputeStatus), default=DisputeStatus.OPEN, nullable=False)
    resolution_note = Column(Text, nullable=True)
    resolved_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    ledger_event_id = Column(String(36), ForeignKey("ledger_events.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class AdminAction(Base):
    __tablename__ = "admin_actions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    admin_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    action_type = Column(Enum(AdminActionType), nullable=False)
    target_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    target_device_id = Column(String(40), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

# --- 6. ESG INCENTIVES (Custom Additions) ---
class RewardPoint(Base):
    __tablename__ = "reward_points"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    points = Column(Integer, nullable=False)
    action = Column(String(60), nullable=False)
    device_id = Column(String(40), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
class RecyclingRecord(Base):
    __tablename__ = "recycling_records"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    certificate_id = Column(String(60), unique=True, nullable=True)
    device_id = Column(String(40), ForeignKey("devices.id"), nullable=False)
    recycler_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    recycling_status = Column(Enum(RecyclingStatus), default=RecyclingStatus.SENT, nullable=False)
    received_date = Column(DateTime(timezone=True), nullable=True)
    completion_date = Column(DateTime(timezone=True), nullable=True)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()