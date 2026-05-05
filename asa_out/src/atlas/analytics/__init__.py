"""
atlas.analytics — Advanced Analytics & Pattern Detection

Public surface:
    AdvancedAnalyticsService  — all analytics computation methods
    AnalyticsFilters          — filter dataclass for scoping queries
    router                    — FastAPI APIRouter
"""
from atlas.analytics.service import AdvancedAnalyticsService, AnalyticsFilters
from atlas.analytics import router as analytics_router

__all__ = ["AdvancedAnalyticsService", "AnalyticsFilters", "analytics_router"]
