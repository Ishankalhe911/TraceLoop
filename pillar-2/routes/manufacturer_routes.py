from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Import RBAC
from database import User, UserRole
from auth import RequireRole

router = APIRouter(prefix="/manufacturer", tags=["Manufacturer Database"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success", "code": code, "data": data or {},
        "error": None, "timestamp": datetime.utcnow().isoformat() + "Z", "traceloop_version": "v1"
    }

# DEMO-ONLY fixture: this is not an OEM API and is not used as verification evidence.
MOCK_MANUFACTURER_DB = {
    "DL-XYZ123": {
        "brand": "Dell", "model": "Latitude 5420",
        "original_config": {"cpu": "Intel Core i5-1145G7", "ram": "16GB", "storage": "512GB SSD"}
    },
    "HP-ABC789": {
        "brand": "HP", "model": "EliteBook 840 G8",
        "original_config": {"cpu": "Intel Core i5-1135G7", "ram": "16GB", "storage": "512GB SSD"}
    }
}

@router.get("/lookup/{brand_code}/{serial_raw}")
def lookup_manufacturer_serial(
    brand_code: str, 
    serial_raw: str,
    # SOP Rule: Only Verifiable nodes can query manufacturer DBs during inspection
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE])) 
):
    """Return demo fixture data only; this is not a live OEM lookup or proof of authenticity."""
    lookup_key = f"{brand_code.upper()}-{serial_raw.upper()}"
    
    device_data = MOCK_MANUFACTURER_DB.get(lookup_key)
    
    if not device_data:
        raise HTTPException(status_code=404, detail="Serial number not found in manufacturer records.")
        
    return standard_response({
        **device_data,
        "source_type": "DEMO_FIXTURE_NOT_OEM_VERIFIED",
        "verification_usable": False,
        "notice": "Demonstration data only. This response must not be treated as manufacturer confirmation or verification evidence.",
    })
