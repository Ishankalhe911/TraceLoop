"""
analytics_routes.py — Trace-Loop Pillar 2
Regulator-facing analytics layer.

Provides lifecycle and circularity analytics relevant to IEEE ClimateChain Track 3.
This is not a claim of formal UN COP 31 compliance. Environmental impact figures are limited to directly observed events; no avoided-CO2e
or e-waste mass is inferred without device-specific evidence and a documented method.

Metrics are derived from existing Device, LedgerEvent, Transfer,
Dispute, DeviceHardwareLog, and RecyclingRecord tables. V1 legacy history is
segregated from confirmed V2 operational metrics. No blockchain transactions are sent.

Endpoints:
  GET /analytics/overview     — Full system snapshot for regulator dashboard
  GET /analytics/lifecycle    — State distribution breakdown
  GET /analytics/recent-events — Latest on-chain events with Lora/explorer links
"""

import os
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from database import (
    get_db, Device, DeviceStatus, RegistrationStatus, IdentityVersion, LedgerEvent,
    Transfer, TransferStatus, Dispute, DisputeStatus,
    DeviceHardwareLog, RecyclingRecord, User, UserRole
)
from auth import RequireRole

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
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Complete system analytics overview.
    Provides operational lifecycle analytics and accountable circularity counts:
      - Total device counts by lifecycle state
      - Hardware change activity
      - Dispute statistics
      - Recycling rate and recent activity
      - On-chain event summary
      - Eco-Points distribution totals
      - Network participant counts
    """

    # ── DEVICE STATE COUNTS ──────────────────────────────────────────────
    # Legacy Phase 1 test passports stay available for history, but they are not
    # counted as current registered devices in operational lifecycle metrics.
    legacy_filter = or_(
        Device.identity_version == IdentityVersion.V1_LEGACY,
        Device.registration_status == RegistrationStatus.LEGACY_TEST,
    )
    operational_filter = (
        Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
        Device.registration_status == RegistrationStatus.CONFIRMED,
    )
    legacy_test_devices = (
        db.query(func.count(Device.id)).filter(legacy_filter).scalar() or 0
    )
    total_devices = (
        db.query(func.count(Device.id)).filter(*operational_filter).scalar() or 0
    )
    pending_registrations = (
        db.query(func.count(Device.id))
        .filter(
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
            Device.registration_status == RegistrationStatus.PENDING,
        ).scalar() or 0
    )
    failed_registrations = (
        db.query(func.count(Device.id))
        .filter(
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
            Device.registration_status == RegistrationStatus.FAILED,
        ).scalar() or 0
    )
    reconciliation_required = (
        db.query(func.count(Device.id))
        .filter(
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
            Device.registration_status == RegistrationStatus.RECONCILIATION_REQUIRED,
        ).scalar() or 0
    )

    state_counts = dict(
        db.query(Device.status, func.count(Device.id))
        .filter(*operational_filter)
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
    total_hardware_changes = (
        db.query(func.count(DeviceHardwareLog.id))
        .join(Device, Device.id == DeviceHardwareLog.device_id)
        .filter(*operational_filter)
        .scalar() or 0
    )

    # Devices that have at least one hardware change declared in the active V2 set.
    devices_with_hw_changes = (
        db.query(func.count(func.distinct(DeviceHardwareLog.device_id)))
        .join(Device, Device.id == DeviceHardwareLog.device_id)
        .filter(*operational_filter)
        .scalar() or 0
    )

    # ── DISPUTE STATISTICS: operational V2 only ──────────────────────────
    total_disputes = (
        db.query(func.count(Dispute.id))
        .join(Device, Device.id == Dispute.device_id)
        .filter(*operational_filter)
        .scalar() or 0
    )

    dispute_state_counts = dict(
        db.query(Dispute.status, func.count(Dispute.id))
        .join(Device, Device.id == Dispute.device_id)
        .filter(*operational_filter)
        .group_by(Dispute.status)
        .all()
    )

    disputes_under_review = dispute_state_counts.get(DisputeStatus.UNDER_REVIEW, 0)
    disputes_open         = dispute_state_counts.get(DisputeStatus.OPEN, 0)
    disputes_resolved     = dispute_state_counts.get(DisputeStatus.RESOLVED, 0)
    disputes_dismissed    = dispute_state_counts.get(DisputeStatus.DISMISSED, 0)

    # ── TRANSFER STATISTICS ──────────────────────────────────────────────
    transfer_query = db.query(Transfer).join(Device, Device.id == Transfer.device_id).filter(*operational_filter)
    total_transfers = transfer_query.count()
    completed_transfers = (
        db.query(func.count(Transfer.id))
        .join(Device, Device.id == Transfer.device_id)
        .filter(*operational_filter, Transfer.status == TransferStatus.COMPLETED)
        .scalar() or 0
    )

    # Climate & circularity: report only observed, auditable counts here.
    # A transfer is not itself proof of a counterfactual CO2e saving, and a
    # recycling status is not proof of device mass or recovered-material yield.
    confirmed_recycling_count = (
        db.query(func.count(RecyclingRecord.id))
        .join(Device, Device.id == RecyclingRecord.device_id)
        .filter(
            *operational_filter,
            RecyclingRecord.recycling_status == "RECYCLED",
            RecyclingRecord.completion_date.isnot(None),
        )
        .scalar() or 0
    )
    climate_circularity = {
        "completed_transfer_count": int(completed_transfers),
        "confirmed_recycling_completion_count": int(confirmed_recycling_count),
        "measured_e_waste_mass_kg": None,
        "estimated_co2e_avoided_kg": None,
        "methodology_status": "COUNTS_ONLY_NO_UNVALIDATED_IMPACT_ESTIMATES",
        "notes": [
            "Completed transfers are observed lifecycle events, not proof of avoided new-device production.",
            "Recycling completion counts require a recorded RECYCLED status and completion timestamp.",
            "Mass and avoided CO2e are not calculated because device-specific weights, recovery yields, and a validated life-cycle methodology are not currently recorded.",
        ],
    }

    # Fraud flags
    velocity_flagged = (
        db.query(func.count(Transfer.id))
        .join(Device, Device.id == Transfer.device_id)
        .filter(*operational_filter, Transfer.velocity_flagged == True)
        .scalar() or 0
    )
    count_flagged = (
        db.query(func.count(Transfer.id))
        .join(Device, Device.id == Transfer.device_id)
        .filter(*operational_filter, Transfer.count_flagged == True)
        .scalar() or 0
    )

    # ── RECENT RECYCLING (last 30 days) ──────────────────────────────────
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_recycling = (
        db.query(func.count(RecyclingRecord.id))
        .join(Device, Device.id == RecyclingRecord.device_id)
        .filter(
            *operational_filter,
            RecyclingRecord.recycling_status == "RECYCLED",
            RecyclingRecord.completion_date >= thirty_days_ago
        )
        .scalar() or 0
    )

    # ── ON-CHAIN EVENT COUNTS ─────────────────────────────────────────────
    total_chain_events = (
        db.query(func.count(LedgerEvent.id))
        .join(Device, Device.id == LedgerEvent.device_id)
        .filter(*operational_filter)
        .scalar() or 0
    )
    legacy_history_events = (
        db.query(func.count(LedgerEvent.id))
        .join(Device, Device.id == LedgerEvent.device_id)
        .filter(legacy_filter)
        .scalar() or 0
    )

    event_type_counts = dict(
        db.query(LedgerEvent.event_type, func.count(LedgerEvent.id))
        .join(Device, Device.id == LedgerEvent.device_id)
        .filter(*operational_filter)
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
        .filter(
            Device.is_for_sale == True,
            Device.stamp_valid == True,
            Device.registration_status == RegistrationStatus.CONFIRMED,
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
        )
        .scalar() or 0
    )

    return standard_response({

        # ── Summary ─────────────────────────────────────────────────────
        "climate_circularity_impact": climate_circularity,

        "summary": {
            "total_devices_registered": total_devices,
            "legacy_test_records": legacy_test_devices,
            "legacy_history_events": legacy_history_events,
            "pending_registrations": pending_registrations,
            "failed_registrations": failed_registrations,
            "reconciliation_required": reconciliation_required,
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
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Detailed per-brand and per-state breakdown.
    Useful for regulator drill-down by brand category.
    """
    # Per-brand device counts
    lifecycle_legacy_filter = or_(
        Device.identity_version == IdentityVersion.V1_LEGACY,
        Device.registration_status == RegistrationStatus.LEGACY_TEST,
    )
    legacy_test_devices = (
        db.query(func.count(Device.id))
        .filter(lifecycle_legacy_filter)
        .scalar() or 0
    )
    brand_counts = (
        db.query(Device.brand_name, func.count(Device.id))
        .filter(
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
            Device.registration_status == RegistrationStatus.CONFIRMED,
        )
        .group_by(Device.brand_name)
        .order_by(func.count(Device.id).desc())
        .all()
    )

    # Per-brand recycling counts
    brand_recycled = (
        db.query(Device.brand_name, func.count(Device.id))
        .filter(
            Device.status == DeviceStatus.RECYCLED,
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
            Device.registration_status == RegistrationStatus.CONFIRMED,
        )
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
        .filter(
            Device.stamp_valid == True,
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
            Device.registration_status == RegistrationStatus.CONFIRMED,
        )
        .scalar() or 0
    )
    invalid_stamps = (
        db.query(func.count(Device.id))
        .filter(
            Device.stamp_valid == False,
            Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
            Device.registration_status == RegistrationStatus.CONFIRMED,
        )
        .scalar() or 0
    )

    return standard_response({
        "legacy_test_records": legacy_test_devices,
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
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
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
        identity_version = (
            device.identity_version.value if device and hasattr(device.identity_version, "value")
            else (str(device.identity_version) if device else None)
        )
        metadata = e.metadata_ if isinstance(e.metadata_, dict) else {}
        historical_chain_id = metadata.get("public_id") if identity_version == IdentityVersion.V1_LEGACY.value else None
        results.append({
            "event_id": e.id,
            "device_id": device.public_id if device else None,
            "identity_version": identity_version,
            "historical_chain_id": historical_chain_id,
            "event_type": e.event_type,
            "actor_type": e.actor_type,
            "chain_status": e.chain_status.value if hasattr(e.chain_status, "value") else e.chain_status,
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
