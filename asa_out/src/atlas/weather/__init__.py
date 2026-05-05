"""
atlas.weather — Weather Context Integration

Public surface:
    WeatherContextService  — CRUD + rebuild + confidence for weather observations
    parse_metar            — stdlib-only METAR field extractor
    router                 — FastAPI APIRouter (weather endpoints)
"""
from atlas.weather.service import WeatherContextService
from atlas.weather.metar_parser import parse_metar
from atlas.weather import router as weather_router

__all__ = ["WeatherContextService", "parse_metar", "weather_router"]
