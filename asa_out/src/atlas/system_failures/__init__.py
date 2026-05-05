"""
atlas.system_failures — Mechanical / System Failure Tracking

Public surface:
    SystemFailureTrackingService  — CRUD, confidence, conflict detection, analytics
    router                         — FastAPI APIRouter (all system-failure endpoints)
"""
from atlas.system_failures.service import SystemFailureTrackingService
from atlas.system_failures import router as system_failures_router

__all__ = ["SystemFailureTrackingService", "system_failures_router"]
