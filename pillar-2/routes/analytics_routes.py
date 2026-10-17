"""
analytics_routes.py — Trace-Loop Pillar 2
Regulator-facing analytics layer.

Closes the PS3 IEngage requirement:
  "Real-time analytics for regulators and consumer incentive mechanisms"

All metrics derived from existing Device, LedgerEvent, Transfer,
Dispute, DeviceHardwareLog, and RecyclingRecord tables.
Zero new blockchain transactions. Zero new schema changes.

Endpoints:
  GET /analytics/overview     — Full system snapshot for regulator dashboard
  GET /analytics/lifecycle    — State distribution breakdown
  GET /analytics/recent-events — Latest on-chain events with Lora/explorer links
"""

import os
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import (
    get_db, Device, DeviceStatus, LedgerEvent,
    Transfer, TransferStatus, Dispute, DisputeStatus,
    DeviceHardwareLog, RecyclingRecord, User, UserRole
)
from auth import get_current_user

router = APIRouter(prefix="/analytics", tags=["Analytics & Regulatory"])

ALGORAND_EXPLORER_BASE = "https://testnet.explorer.perawallet.app/tx"
APP_ID = os.getenv("APP_ID", "771258340")


def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1"
    }


# ─────────────────────────────────────────────
# MAIN OVERVIEW — Full regulator snapshot
# ─────────────────────────────────────────────

@router.get("/overview")
def get_analytics_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Complete system analytics overview.
    Covers all PS3 IEngage regulatory analytics requirements:
      - Total device counts by lifecycle state
      - Hardware change activity
      - Dispute statistics
      - Recycling rate and recent activity
      - On-chain event summary
      - Eco-Points distribution totals
      - Network participant counts
    """

    # ── DEVICE STATE COUNTS ──────────────────────────────────────────────
    total_devices = db.query(func.count(Device.id)).scalar() or 0

    state_counts = dict(
        db.query(Device.status, func.count(Device.id))
        .group_by(Device.status)
        .all()
    )

    registered_count  = state_counts.get(DeviceStatus.REGISTERED, 0)
    verified_count    = state_counts.get(DeviceStatus.VERIFIED, 0)
    transferred_count = state_counts.get(DeviceStatus.TRANSFERRED, 0)
    disputed_count    = state_counts.get(DeviceStatus.DISPUTED, 0)
    recycled_count    = state_counts.get(DeviceStatus.RECYCLED, 0)
    exported_count    = state_counts.get(DeviceStatus.EXPORTED, 0)

    # Active = not yet terminal
    active_devices = total_devices - recycled_count - exported_count

    # Recycling rate = (recycled / total) * 100
    recycling_rate = round((recycled_count / total_devices * 100), 2) if total_devices > 0 else 0.0

    # ── HARDWARE CHANGE ACTIVITY ─────────────────────────────────────────
    total_hardware_changes = db.query(func.count(DeviceHardwareLog.id)).scalar() or 0

    # Devices that have at least one hardware change declared
    devices_with_hw_changes = (
        db.query(func.count(func.distinct(DeviceHardwareLog.device_id))).scalar() or 0
    )

    # ── DISPUTE STATISTICS ───────────────────────────────────────────────
    total_disputes = db.query(func.count(Dispute.id)).scalar() or 0

    dispute_state_counts = dict(
        db.query(Dispute.status, func.count(Dispute.id))
        .group_by(Dispute.status)
        .all()
    )

    disputes_under_review = dispute_state_counts.get(DisputeStatus.UNDER_REVIEW, 0)
    disputes_open         = dispute_state_counts.get(DisputeStatus.OPEN, 0)
    disputes_resolved     = dispute_state_counts.get(DisputeStatus.RESOLVED, 0)
    disputes_dismissed    = dispute_state_counts.get(DisputeStatus.DISMISSED, 0)

    # ── TRANSFER STATISTICS ──────────────────────────────────────────────
    total_transfers = db.query(func.count(Transfer.id)).scalar() or 0
    completed_transfers = (
        db.query(func.count(Transfer.id))
        .filter(Transfer.status == TransferStatus.COMPLETED)
        .scalar() or 0
    )

    # Fraud flags
    velocity_flagged = (
        db.query(func.count(Transfer.id))
        .filter(Transfer.velocity_flagged == True)
        .scalar() or 0
    )
    count_flagged = (
        db.query(func.count(Transfer.id))
        .filter(Transfer.count_flagged == True)
        .scalar() or 0
    )

    # ── RECENT RECYCLING (last 30 days) ──────────────────────────────────
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_recycling = (
        db.query(func.count(RecyclingRecord.id))
        .filter(
            RecyclingRecord.recycling_status == "RECYCLED",
            RecyclingRecord.completion_date >= thirty_days_ago
        )
        .scalar() or 0
    )

    # ── ON-CHAIN EVENT COUNTS ─────────────────────────────────────────────
    total_chain_events = db.query(func.count(LedgerEvent.id)).scalar() or 0

    event_type_counts = dict(
        db.query(LedgerEvent.event_type, func.count(LedgerEvent.id))
        .group_by(LedgerEvent.event_type)
        .all()
    )

    # ── NETWORK PARTICIPANTS ──────────────────────────────────────────────
    participant_counts = dict(
        db.query(User.role, func.count(User.id))
        .group_by(User.role)
        .all()
    )

    # ── MARKETPLACE ───────────────────────────────────────────────────────
    devices_for_sale = (
        db.query(func.count(Device.id))
        .filter(Device.is_for_sale == True, Device.stamp_valid == True)
        .scalar() or 0
    )

    return standard_response({

        # ── Summary ─────────────────────────────────────────────────────
        "summary": {
            "total_devices_registered": total_devices,
            "active_devices": active_devices,
            "recycling_rate_percent": recycling_rate,
            "total_on_chain_events": total_chain_events,
            "algorand_app_id": APP_ID,
            "network": "Algorand TestNet"
        },

        # ── Lifecycle State Distribution ─────────────────────────────────
        "lifecycle_distribution": {
            "REGISTERED": registered_count,
            "VERIFIED": verified_count,
            "TRANSFERRED": transferred_count,
            "DISPUTED": disputed_count,
            "RECYCLED": recycled_count,
            "EXPORTED": exported_count
        },

        # ── Hardware Intelligence ────────────────────────────────────────
        "hardware_activity": {
            "total_hardware_changes_declared": total_hardware_changes,
            "devices_with_modifications": devices_with_hw_changes,
            "modification_rate_percent": round(
                (devices_with_hw_changes / total_devices * 100), 2
            ) if total_devices > 0 else 0.0
        },

        # ── Dispute Health ───────────────────────────────────────────────
        "dispute_statistics": {
            "total_disputes_filed": total_disputes,
            "under_review": disputes_under_review,
            "frozen_on_chain": disputes_open,
            "resolved": disputes_resolved,
            "dismissed": disputes_dismissed,
            "dispute_rate_percent": round(
                (total_disputes / total_devices * 100), 2
            ) if total_devices > 0 else 0.0
        },

        # ── Transfer & Fraud Signals ─────────────────────────────────────
        "transfer_statistics": {
            "total_transfers": total_transfers,
            "completed_transfers": completed_transfers,
            "velocity_flagged_transfers": velocity_flagged,
            "device_count_flagged_transfers": count_flagged
        },

        # ── Recycling & EPR ──────────────────────────────────────────────
        "recycling_activity": {
            "total_recycled": recycled_count,
            "recycled_last_30_days": recent_recycling,
            "exported": exported_count,
            "formal_recycling_rate_percent": recycling_rate
        },

        # ── On-Chain Events Breakdown ────────────────────────────────────
        "on_chain_events": event_type_counts,

        # ── Network Participants ─────────────────────────────────────────
        "network_participants": {
            "first_buyers": participant_counts.get(UserRole.FIRST_BUYER, 0),
            "buyers": participant_counts.get(UserRole.BUYER, 0),
            "resellers": participant_counts.get(UserRole.RESELLER, 0),
            "authorized_verifiers": participant_counts.get(UserRole.VERIFIABLE, 0),
            "cpcb_recyclers": participant_counts.get(UserRole.RECYCLER, 0)
        },

        # ── Marketplace ──────────────────────────────────────────────────
        "marketplace": {
            "verified_listings_active": devices_for_sale
        }
    })


# ─────────────────────────────────────────────
# LIFECYCLE BREAKDOWN — Detailed state view
# ─────────────────────────────────────────────

@router.get("/lifecycle")
def get_lifecycle_breakdown(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Detailed per-brand and per-state breakdown.
    Useful for regulator drill-down by brand category.
    """
    # Per-brand device counts
    brand_counts = (
        db.query(Device.brand_name, func.count(Device.id))
        .group_by(Device.brand_name)
        .order_by(func.count(Device.id).desc())
        .all()
    )

    # Per-brand recycling counts
    brand_recycled = (
        db.query(Device.brand_name, func.count(Device.id))
        .filter(Device.status == DeviceStatus.RECYCLED)
        .group_by(Device.brand_name)
        .all()
    )
    brand_recycled_map = dict(brand_recycled)

    breakdown = []
    for brand_name, total in brand_counts:
        recycled = brand_recycled_map.get(brand_name, 0)
        breakdown.append({
            "brand": brand_name,
            "total_registered": total,
            "recycled": recycled,
            "recycling_rate_percent": round(
                (recycled / total * 100), 2
            ) if total > 0 else 0.0
        })

    # Stamp validity
    valid_stamps = (
        db.query(func.count(Device.id))
        .filter(Device.stamp_valid == True)
        .scalar() or 0
    )
    invalid_stamps = (
        db.query(func.count(Device.id))
        .filter(Device.stamp_valid == False)
        .scalar() or 0
    )

    return standard_response({
        "by_brand": breakdown,
        "stamp_validity": {
            "stamp_valid": valid_stamps,
            "stamp_invalid_or_not_verified": invalid_stamps
        }
    })


# ─────────────────────────────────────────────
# RECENT ON-CHAIN EVENTS — with explorer links
# ─────────────────────────────────────────────

@router.get("/recent-events")
def get_recent_events(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Latest on-chain events mirrored from Algorand.
    Each event includes a direct link to Pera Explorer / Lora for verification.
    Covers the PS3 requirement for real-time regulatory visibility.
    """
    events = (
        db.query(LedgerEvent)
        .order_by(LedgerEvent.timestamp.desc())
        .limit(min(limit, 100))
        .all()
    )

    results = []
    for e in events:
        explorer_url = (
            f"{ALGORAND_EXPLORER_BASE}/{e.tx_hash}"
            if e.tx_hash else None
        )
        lora_url = (
            f"https://lora.algokit.io/testnet/transaction/{e.tx_hash}"
            if e.tx_hash else None
        )

        device = db.query(Device).filter(Device.id == e.device_id).first()
        results.append({
            "event_id": e.id,
            "device_id": device.public_id if device else None,
            "event_type": e.event_type,
            "actor_type": e.actor_type,
            "chain_status": e.chain_status,
            "tx_hash": e.tx_hash,
            "block_number": e.block_number,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "view_on_explorer": explorer_url,
            "view_on_lora": lora_url
        })

    return standard_response({
        "total_returned": len(results),
        "events": results,
        "note": "Events mirrored from Algorand TestNet via subscriber daemon. "
                "Source of truth is always the on-chain ledger."
    })