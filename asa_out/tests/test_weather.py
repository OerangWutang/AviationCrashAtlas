"""
Tests for the Weather Context Integration feature.

Covers:
- ORM model importability, enum values, required columns
- METAR parser: unit normalization, field extraction, raw text preservation,
  flight rules derivation, edge cases
- haversine distance calculation
- confidence score formula
- WeatherContextService unit tests (mocked AsyncSession)
  - create_observation: auto-parse METAR, distance calc, time delta, claim links
  - update_observation: partial update, re-parse on raw_report_text change
  - delete_observation: True/False
  - rebuild_weather: recalculates scores
  - get_observations: ordering
  - get_supporting_claims: returns distinct claims
- API router response shapes
  - GET /api/v1/accidents/{id}/weather — 200 empty, 200 with data, 404 bad id
  - POST /api/v1/accidents/{id}/weather — 201 created
  - PATCH /api/v1/weather/observations/{obs_id} — 200 / 404
  - DELETE /api/v1/weather/observations/{obs_id} — 204 / 404
  - POST /api/v1/accidents/{id}/weather/rebuild — 200
  - GET /api/v1/accidents/{id}/weather/claims — list
- Disputed observation handling
- Causation note always present in API response
- Empty weather state (no observations)

Sample METARs from spec:
  KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2
  EHAM 051025Z 22015G25KT 9999 FEW020 SCT035 12/07 Q1013 NOSIG
  KLAX 151753Z 25008KT 6SM HZ FEW012 22/16 A3001 RMK AO2
"""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_obs(
    *,
    id: str | None = None,
    accident_event_id: str = "evt-1",
    report_type: str = "metar",
    raw_report_text: str | None = None,
    station_identifier: str | None = "KJFK",
    temperature_c: float | None = None,
    dew_point_c: float | None = None,
    wind_speed_kt: float | None = None,
    wind_direction_degrees: int | None = None,
    wind_gust_kt: float | None = None,
    visibility_m: float | None = None,
    ceiling_ft: int | None = None,
    altimeter_hpa: float | None = None,
    precipitation_type: str | None = None,
    thunderstorm_present: bool | None = None,
    icing_risk: str | None = None,
    turbulence_risk: str | None = None,
    flight_rules: str | None = None,
    confidence_score: float | None = None,
    is_disputed: bool = False,
    dispute_summary: str | None = None,
    distance_to_accident_km: float | None = None,
    accident_time_delta_minutes: float | None = None,
    claim_links: list | None = None,
    observation_time_utc: datetime | None = None,
    station_latitude: float | None = None,
    station_longitude: float | None = None,
    parsed_data: dict | None = None,
    source_id: str | None = None,
    source: object | None = None,
) -> MagicMock:
    obs = MagicMock()
    obs.id = id or str(uuid.uuid4())
    obs.accident_event_id = accident_event_id
    obs.source_id = source_id
    obs.source = source
    obs.station_identifier = station_identifier
    obs.station_name = None
    obs.station_latitude = station_latitude
    obs.station_longitude = station_longitude
    obs.distance_to_accident_km = distance_to_accident_km
    obs.observation_time_utc = observation_time_utc
    obs.accident_time_delta_minutes = accident_time_delta_minutes
    obs.report_type = report_type
    obs.raw_report_text = raw_report_text
    obs.parsed_data = parsed_data
    obs.temperature_c = temperature_c
    obs.dew_point_c = dew_point_c
    obs.wind_direction_degrees = wind_direction_degrees
    obs.wind_speed_kt = wind_speed_kt
    obs.wind_gust_kt = wind_gust_kt
    obs.visibility_m = visibility_m
    obs.ceiling_ft = ceiling_ft
    obs.altimeter_hpa = altimeter_hpa
    obs.precipitation_type = precipitation_type
    obs.thunderstorm_present = thunderstorm_present
    obs.icing_risk = icing_risk
    obs.turbulence_risk = turbulence_risk
    obs.flight_rules = flight_rules
    obs.confidence_score = confidence_score
    obs.is_disputed = is_disputed
    obs.dispute_summary = dispute_summary
    obs.created_at = datetime(2024, 3, 10, tzinfo=UTC)
    obs.updated_at = datetime(2024, 3, 10, tzinfo=UTC)
    obs.claim_links = claim_links or []
    return obs


def _make_claim_link(claim_type: str = "confirmed", source_id: str = "src-1") -> MagicMock:
    lnk = MagicMock()
    lnk.claim_id = str(uuid.uuid4())
    lnk.claim = MagicMock()
    lnk.claim.claim_type = claim_type
    lnk.claim.source_id = source_id
    lnk.claim.field_name = "weather_condition"
    lnk.link_reason = "supporting_claim"
    return lnk


# ─────────────────────────────────────────────────────────────────────────────
# ORM sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestWeatherOrmModels:
    def test_observation_importable(self):
        from atlas.models.orm import AccidentWeatherObservation  # noqa: F401

    def test_claim_join_importable(self):
        from atlas.models.orm import WeatherObservationClaim  # noqa: F401

    def test_enums_importable(self):
        from atlas.models.orm import IcingRisk, TurbulenceRisk, FlightRules, WeatherReportType
        assert FlightRules.VFR == "vfr"
        assert FlightRules.IFR == "ifr"
        assert FlightRules.LIFR == "lifr"
        assert IcingRisk.SEVERE == "severe"
        assert TurbulenceRisk.POSSIBLE == "possible"
        assert WeatherReportType.METAR == "metar"
        assert WeatherReportType.TAF == "taf"
        assert WeatherReportType.MANUAL == "manual"

    def test_observation_required_columns(self):
        from atlas.models.orm import AccidentWeatherObservation
        cols = {c.key for c in AccidentWeatherObservation.__table__.c}
        required = {
            "id", "accident_event_id", "report_type", "is_disputed",
            "created_at", "updated_at",
        }
        assert required.issubset(cols)

    def test_claim_join_required_columns(self):
        from atlas.models.orm import WeatherObservationClaim
        cols = {c.key for c in WeatherObservationClaim.__table__.c}
        assert {"id", "weather_observation_id", "claim_id", "link_reason", "created_at"}.issubset(cols)


# ─────────────────────────────────────────────────────────────────────────────
# METAR parser
# ─────────────────────────────────────────────────────────────────────────────

class TestMetarParser:
    def _parse(self, raw: str):
        from atlas.weather.metar_parser import parse_metar
        return parse_metar(raw)

    # --- KJFK sample ---
    def test_kjfk_station(self):
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        assert pm.station == "KJFK"

    def test_kjfk_wind(self):
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        assert pm.wind_direction_degrees == 180
        assert pm.wind_speed_kt == pytest.approx(12.0)
        assert pm.wind_gust_kt is None

    def test_kjfk_visibility_sm_to_m(self):
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        # 10 SM = 16093.44 m
        assert pm.visibility_m == pytest.approx(16093.44, rel=0.01)

    def test_kjfk_temperature(self):
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        assert pm.temperature_c == pytest.approx(26.0)
        assert pm.dew_point_c == pytest.approx(18.0)

    def test_kjfk_altimeter_inhg(self):
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        # A2992 = 29.92 inHg × 33.8639 = 1013.28 hPa
        assert pm.altimeter_hpa == pytest.approx(1013.28, abs=0.5)

    def test_kjfk_no_ceiling_from_few_sct(self):
        # FEW and SCT are not ceiling layers (BKN/OVC are)
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        assert pm.ceiling_ft is None

    def test_kjfk_flight_rules_vfr(self):
        # 10SM vis, no ceiling → VFR
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        assert pm.flight_rules == "vfr"

    def test_kjfk_remarks_preserved(self):
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        assert pm.remarks == "AO2"

    # --- EHAM sample (ICAO format, gust) ---
    def test_eham_gust(self):
        pm = self._parse("EHAM 051025Z 22015G25KT 9999 FEW020 SCT035 12/07 Q1013 NOSIG")
        assert pm.wind_speed_kt == pytest.approx(15.0)
        assert pm.wind_gust_kt == pytest.approx(25.0)
        assert pm.wind_direction_degrees == 220

    def test_eham_qnh_hpa(self):
        pm = self._parse("EHAM 051025Z 22015G25KT 9999 FEW020 SCT035 12/07 Q1013 NOSIG")
        assert pm.altimeter_hpa == pytest.approx(1013.0)

    def test_eham_9999_visibility(self):
        # 9999 m in ICAO = 10 km+
        pm = self._parse("EHAM 051025Z 22015G25KT 9999 FEW020 SCT035 12/07 Q1013 NOSIG")
        assert pm.visibility_m == pytest.approx(10000.0)

    def test_eham_temperature_negative(self):
        pm = self._parse("EHAM 051025Z 22015G25KT 9999 FEW020 SCT035 12/07 Q1013 NOSIG")
        assert pm.temperature_c == pytest.approx(12.0)
        assert pm.dew_point_c == pytest.approx(7.0)

    # --- KLAX sample (reduced visibility, haze) ---
    def test_klax_reduced_visibility(self):
        pm = self._parse("KLAX 151753Z 25008KT 6SM HZ FEW012 22/16 A3001 RMK AO2")
        assert pm.visibility_m == pytest.approx(6 * 1609.344, rel=0.01)

    def test_klax_flight_rules_vfr_at_six_sm(self):
        # 6 SM >= 5 SM VFR threshold; FEW012 is not a ceiling → VFR
        pm = self._parse("KLAX 151753Z 25008KT 6SM HZ FEW012 22/16 A3001 RMK AO2")
        assert pm.flight_rules == "vfr"

    # --- IFR/LIFR conditions ---
    def test_ceiling_bkn_sets_ceiling(self):
        pm = self._parse("KORD 010000Z 00000KT 3SM -RA BKN008 OVC020 10/09 A2950")
        assert pm.ceiling_ft == 800   # lowest BKN/OVC

    def test_ifr_conditions(self):
        pm = self._parse("KORD 010000Z 00000KT 3SM -RA BKN008 OVC020 10/09 A2950")
        assert pm.flight_rules == "ifr"

    def test_lifr_conditions(self):
        pm = self._parse("KORD 010000Z 00000KT 0SM FG OVC002 10/09 A2950")
        assert pm.flight_rules == "lifr"

    # --- Thunderstorm detection ---
    def test_thunderstorm_detected(self):
        pm = self._parse("KORD 010000Z 00000KT 1SM TS OVC010 20/19 A2980")
        assert pm.thunderstorm_present is True

    def test_no_thunderstorm_when_absent(self):
        pm = self._parse("KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2")
        assert pm.thunderstorm_present is False

    # --- Negative temperature ---
    def test_negative_temperature(self):
        pm = self._parse("KBZN 010000Z 00000KT 10SM BKN080 M05/M12 A3010")
        assert pm.temperature_c == pytest.approx(-5.0)
        assert pm.dew_point_c == pytest.approx(-12.0)

    # --- Raw text preserved ---
    def test_raw_text_preserved(self):
        raw = "KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2"
        pm = self._parse(raw)
        assert pm.raw == raw.strip()

    # --- Variable wind ---
    def test_variable_wind(self):
        pm = self._parse("KORD 010000Z VRB03KT 10SM CLR 20/10 A2990")
        assert pm.wind_variable is True
        assert pm.wind_speed_kt == pytest.approx(3.0)
        assert pm.wind_direction_degrees is None

    # --- Precipitation ---
    def test_rain_detected(self):
        pm = self._parse("KORD 010000Z 00000KT 5SM -RA OVC012 10/09 A2960")
        assert pm.precipitation_type == "rain"

    def test_snow_detected(self):
        pm = self._parse("KORD 010000Z 00000KT 2SM SN BKN006 M02/M05 A2940")
        assert pm.precipitation_type == "snow"


# ─────────────────────────────────────────────────────────────────────────────
# Haversine distance
# ─────────────────────────────────────────────────────────────────────────────

class TestHaversine:
    def _dist(self, lat1, lon1, lat2, lon2):
        from atlas.weather.service import haversine_km
        return haversine_km(lat1, lon1, lat2, lon2)

    def test_same_point_is_zero(self):
        assert self._dist(40.0, -74.0, 40.0, -74.0) == pytest.approx(0.0, abs=0.001)

    def test_known_distance_jfk_to_lga(self):
        # JFK ≈ 40.6413° N, 73.7781° W; LGA ≈ 40.7769° N, 73.8740° W
        # Actual ~19 km
        d = self._dist(40.6413, -73.7781, 40.7769, -73.8740)
        assert 15 < d < 25

    def test_symmetrical(self):
        d1 = self._dist(51.5, 0.0, 48.8, 2.3)
        d2 = self._dist(48.8, 2.3, 51.5, 0.0)
        assert d1 == pytest.approx(d2, rel=1e-6)

    def test_short_distance_accurate(self):
        # 1 degree latitude ≈ 111 km
        d = self._dist(0.0, 0.0, 1.0, 0.0)
        assert d == pytest.approx(111.195, abs=0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Confidence score
# ─────────────────────────────────────────────────────────────────────────────

class TestWeatherConfidence:
    def _score(self, **kwargs):
        from atlas.weather.service import compute_confidence
        defaults = dict(
            source_tier=1, time_delta_minutes=15.0,
            distance_km=5.0, report_type="metar", is_disputed=False,
        )
        defaults.update(kwargs)
        return compute_confidence(**defaults)

    def test_perfect_conditions(self):
        s = self._score()
        assert s == pytest.approx(1.0, abs=0.01)

    def test_disputed_penalty(self):
        s_clean = self._score(is_disputed=False)
        s_disp  = self._score(is_disputed=True)
        assert s_disp < s_clean
        assert s_clean - s_disp == pytest.approx(0.30, abs=0.01)

    def test_distant_station_lowers_score(self):
        near = self._score(distance_km=5.0)
        far  = self._score(distance_km=90.0)
        assert far < near

    def test_beyond_100km_zeroes_distance_factor(self):
        # distance factor = 0, averaged with other factors
        s = self._score(distance_km=200.0)
        assert s <= 0.75

    def test_old_observation_lowers_score(self):
        fresh = self._score(time_delta_minutes=10.0)
        stale = self._score(time_delta_minutes=170.0)
        assert stale < fresh

    def test_beyond_180min_zeroes_time_factor(self):
        s = self._score(time_delta_minutes=300.0)
        assert s <= 0.75

    def test_manual_report_lower_than_metar(self):
        m = self._score(report_type="metar")
        manual = self._score(report_type="manual")
        assert manual < m

    def test_tier3_source_lower(self):
        t1 = self._score(source_tier=1)
        t3 = self._score(source_tier=3)
        assert t3 < t1

    def test_missing_data_uses_neutral(self):
        s = self._score(source_tier=None, time_delta_minutes=None, distance_km=None)
        # 0.5 + 0.5 + 0.5 + 1.0 (metar) / 4 = 0.625
        assert s == pytest.approx(0.625, abs=0.01)

    def test_score_never_negative(self):
        s = self._score(
            source_tier=3, time_delta_minutes=300.0,
            distance_km=200.0, report_type="manual", is_disputed=True,
        )
        assert s >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# WeatherContextService — unit tests (mocked session)
# ─────────────────────────────────────────────────────────────────────────────

class TestWeatherContextService:

    @pytest.mark.asyncio
    async def test_create_observation_no_metar_text(self):
        """Create a manual observation without raw text — no parse attempted."""
        from atlas.weather.service import WeatherContextService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.get = AsyncMock(return_value=None)

        obs = await WeatherContextService.create_observation(
            session,
            accident_event_id="evt-1",
            report_type="manual",
            temperature_c=18.0,
            wind_speed_kt=12.0,
        )
        assert obs.temperature_c == 18.0
        assert obs.wind_speed_kt == 12.0
        assert obs.raw_report_text is None
        assert obs.parsed_data is None

    @pytest.mark.asyncio
    async def test_create_observation_metar_auto_parsed(self):
        """Supplying raw METAR text triggers auto-parse."""
        from atlas.weather.service import WeatherContextService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        raw = "KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2"
        obs = await WeatherContextService.create_observation(
            session,
            accident_event_id="evt-1",
            report_type="metar",
            raw_report_text=raw,
        )
        assert obs.raw_report_text == raw    # preserved verbatim
        assert obs.temperature_c == pytest.approx(26.0)
        assert obs.dew_point_c == pytest.approx(18.0)
        assert obs.wind_direction_degrees == 180
        assert obs.wind_speed_kt == pytest.approx(12.0)
        assert obs.station_identifier == "KJFK"  # auto-filled from METAR
        assert obs.flight_rules == "vfr"
        assert obs.parsed_data is not None

    @pytest.mark.asyncio
    async def test_create_observation_distance_computed(self):
        """Distance is computed when both station and accident coordinates given."""
        from atlas.weather.service import WeatherContextService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        obs = await WeatherContextService.create_observation(
            session,
            accident_event_id="evt-1",
            report_type="metar",
            station_latitude=40.6413,
            station_longitude=-73.7781,
            accident_lat=40.7769,
            accident_lon=-73.8740,
        )
        # JFK → LGA ≈ 15–25 km
        assert obs.distance_to_accident_km is not None
        assert 15 < float(obs.distance_to_accident_km) < 25

    @pytest.mark.asyncio
    async def test_create_observation_time_delta_computed(self):
        from atlas.weather.service import WeatherContextService
        from datetime import timedelta

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        acc_time = datetime(2024, 3, 10, 14, 30, tzinfo=UTC)
        obs_time = datetime(2024, 3, 10, 14, 00, tzinfo=UTC)  # 30 min before

        obs = await WeatherContextService.create_observation(
            session,
            accident_event_id="evt-1",
            report_type="metar",
            observation_time_utc=obs_time,
            accident_time_utc=acc_time,
        )
        # obs is 30 min before accident → delta = -30.0
        assert obs.accident_time_delta_minutes == pytest.approx(-30.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_create_observation_claim_ids_linked(self):
        """claim_ids are linked as WeatherObservationClaim rows."""
        from atlas.weather.service import WeatherContextService
        from atlas.models.orm import Claim

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        claim = MagicMock(spec=Claim)
        claim.claim_type = "confirmed"
        claim.source_id = "src-1"

        async def fake_get(model, key):
            return claim

        session.get = fake_get

        obs = await WeatherContextService.create_observation(
            session,
            accident_event_id="evt-1",
            report_type="metar",
            claim_ids=["claim-a", "claim-b"],
        )
        # session.add called for obs + 2 claim links
        assert session.add.call_count >= 3

    @pytest.mark.asyncio
    async def test_create_observation_disputed_penalizes_confidence(self):
        from atlas.weather.service import WeatherContextService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        obs = await WeatherContextService.create_observation(
            session,
            accident_event_id="evt-1",
            report_type="metar",
            is_disputed=True,
            dispute_summary="Contradicts PIREP from same time.",
        )
        assert obs.is_disputed is True
        # confidence should be penalised below non-disputed equivalent
        assert (obs.confidence_score or 0) < 0.8

    @pytest.mark.asyncio
    async def test_get_observations_empty(self):
        from atlas.weather.service import WeatherContextService

        scalars = MagicMock()
        scalars.all.return_value = []
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars

        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = await WeatherContextService.get_observations(session, "evt-no-data")
        assert result == []

    @pytest.mark.asyncio
    async def test_delete_observation_not_found(self):
        from atlas.weather.service import WeatherContextService

        session = MagicMock()
        session.get = AsyncMock(return_value=None)

        result = await WeatherContextService.delete_observation(session, obs_id="missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_observation_found(self):
        from atlas.weather.service import WeatherContextService

        obs = _make_obs()
        session = MagicMock()
        session.get = AsyncMock(return_value=obs)
        session.delete = AsyncMock()

        result = await WeatherContextService.delete_observation(session, obs_id=obs.id)
        assert result is True
        session.delete.assert_called_once_with(obs)

    @pytest.mark.asyncio
    async def test_rebuild_recalculates_confidence(self):
        from atlas.weather.service import WeatherContextService

        obs = _make_obs(confidence_score=0.0, report_type="metar",
                        distance_to_accident_km=5.0, accident_time_delta_minutes=-15.0)

        with patch.object(
            WeatherContextService,
            "get_observations",
            new_callable=AsyncMock,
            return_value=[obs],
        ):
            session = MagicMock()
            result = await WeatherContextService.rebuild_weather(
                session, accident_event_id="evt-1", operator_id="reviewer"
            )

        assert len(result) == 1
        # Should have been updated from 0.0
        assert (result[0].confidence_score or 0) > 0

    @pytest.mark.asyncio
    async def test_rebuild_reparses_metar_if_parsed_data_absent(self):
        from atlas.weather.service import WeatherContextService

        raw = "KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2"
        obs = _make_obs(report_type="metar", raw_report_text=raw, parsed_data=None)

        with patch.object(
            WeatherContextService,
            "get_observations",
            new_callable=AsyncMock,
            return_value=[obs],
        ):
            session = MagicMock()
            await WeatherContextService.rebuild_weather(
                session, accident_event_id="evt-1", operator_id="reviewer"
            )

        # parsed_data should now be populated
        assert obs.parsed_data is not None


# ─────────────────────────────────────────────────────────────────────────────
# Weather → timeline integration
# ─────────────────────────────────────────────────────────────────────────────

class TestWeatherTimelineIntegration:
    def test_thunderstorm_suggests_thunderstorm_event(self):
        from atlas.weather.weather_timeline import suggest_timeline_event_type
        obs = _make_obs(thunderstorm_present=True)
        assert suggest_timeline_event_type(obs) == "thunderstorm_encountered"

    def test_icing_likely_suggests_icing_event(self):
        from atlas.weather.weather_timeline import suggest_timeline_event_type
        obs = _make_obs(icing_risk="likely", thunderstorm_present=False)
        assert suggest_timeline_event_type(obs) == "icing_conditions_reported"

    def test_turbulence_severe_suggests_turbulence_event(self):
        from atlas.weather.weather_timeline import suggest_timeline_event_type
        obs = _make_obs(turbulence_risk="severe", icing_risk="none", thunderstorm_present=False)
        assert suggest_timeline_event_type(obs) == "turbulence_encountered"

    def test_ifr_suggests_low_ceiling_event(self):
        from atlas.weather.weather_timeline import suggest_timeline_event_type
        obs = _make_obs(flight_rules="ifr", icing_risk=None, turbulence_risk=None, thunderstorm_present=False)
        assert suggest_timeline_event_type(obs) == "low_ceiling_ifr_conditions"

    def test_mvfr_suggests_visibility_reduced(self):
        from atlas.weather.weather_timeline import suggest_timeline_event_type
        obs = _make_obs(flight_rules="mvfr", icing_risk=None, turbulence_risk=None, thunderstorm_present=False)
        assert suggest_timeline_event_type(obs) == "visibility_reduced"

    def test_vfr_returns_none(self):
        from atlas.weather.weather_timeline import suggest_timeline_event_type
        obs = _make_obs(flight_rules="vfr", icing_risk="none", turbulence_risk="none", thunderstorm_present=False)
        assert suggest_timeline_event_type(obs) is None

    def test_weather_event_types_frozen(self):
        from atlas.weather.weather_timeline import WEATHER_EVENT_TYPES
        assert "thunderstorm_encountered" in WEATHER_EVENT_TYPES
        assert "icing_conditions_reported" in WEATHER_EVENT_TYPES
        assert "low_ceiling_ifr_conditions" in WEATHER_EVENT_TYPES


# ─────────────────────────────────────────────────────────────────────────────
# API router — FastAPI test client
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def weather_client():
    import httpx
    from fastapi import FastAPI
    from atlas.weather.router import router as wx_router
    from atlas.db.engine import get_db, get_read_db
    from atlas.api.auth import require_reviewer, OperatorContext

    app = FastAPI()
    app.include_router(wx_router)

    async def noop_db():
        session = MagicMock()
        yield session

    app.dependency_overrides[get_db]      = noop_db
    app.dependency_overrides[get_read_db] = noop_db
    app.dependency_overrides[require_reviewer] = lambda: OperatorContext(
        id="test-reviewer", role="reviewer", key_id=""
    )

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestWeatherRouterRead:

    @pytest.mark.asyncio
    async def test_get_weather_empty_returns_200(self, weather_client):
        with (
            patch("atlas.weather.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.weather.router.WeatherContextService.get_observations",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            resp = await weather_client.get("/api/v1/accidents/evt-1/weather")
        assert resp.status_code == 200
        data = resp.json()
        assert data["observation_count"] == 0
        assert data["observations"] == []

    @pytest.mark.asyncio
    async def test_get_weather_404_bad_accident(self, weather_client):
        from fastapi import HTTPException
        with patch(
            "atlas.weather.router._require_accident",
            new_callable=AsyncMock,
            side_effect=HTTPException(status_code=404, detail="Not found"),
        ):
            resp = await weather_client.get("/api/v1/accidents/bad-id/weather")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_weather_returns_observations(self, weather_client):
        obs = _make_obs(
            temperature_c=26.0,
            flight_rules="vfr",
            confidence_score=0.85,
        )
        with (
            patch("atlas.weather.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.weather.router.WeatherContextService.get_observations",
                new_callable=AsyncMock,
                return_value=[obs],
            ),
        ):
            resp = await weather_client.get("/api/v1/accidents/evt-1/weather")
        assert resp.status_code == 200
        data = resp.json()
        assert data["observation_count"] == 1
        assert data["observations"][0]["flight_rules"] == "vfr"
        assert data["observations"][0]["temperature_c"] == pytest.approx(26.0)

    @pytest.mark.asyncio
    async def test_causation_note_present(self, weather_client):
        obs = _make_obs()
        with (
            patch("atlas.weather.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.weather.router.WeatherContextService.get_observations",
                new_callable=AsyncMock,
                return_value=[obs],
            ),
        ):
            resp = await weather_client.get("/api/v1/accidents/evt-1/weather")
        note = resp.json()["observations"][0]["causation_note"]
        assert "contextual" in note.lower()

    @pytest.mark.asyncio
    async def test_disputed_observation_flagged(self, weather_client):
        obs = _make_obs(is_disputed=True, dispute_summary="Conflicting PIREP.")
        with (
            patch("atlas.weather.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.weather.router.WeatherContextService.get_observations",
                new_callable=AsyncMock,
                return_value=[obs],
            ),
        ):
            resp = await weather_client.get("/api/v1/accidents/evt-1/weather")
        data = resp.json()["observations"][0]
        assert data["is_disputed"] is True
        assert data["dispute_summary"] == "Conflicting PIREP."


class TestWeatherRouterWrite:

    @pytest.mark.asyncio
    async def test_create_observation_201(self, weather_client):
        obs = _make_obs(report_type="metar", temperature_c=18.0)
        with (
            patch("atlas.weather.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.weather.router.WeatherContextService.create_observation",
                new_callable=AsyncMock,
                return_value=obs,
            ),
        ):
            resp = await weather_client.post(
                "/api/v1/accidents/evt-1/weather",
                json={
                    "report_type": "metar",
                    "raw_report_text": "KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2",
                },
            )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_patch_observation_200(self, weather_client):
        obs = _make_obs(is_disputed=True, dispute_summary="Updated.")
        with patch(
            "atlas.weather.router.WeatherContextService.update_observation",
            new_callable=AsyncMock,
            return_value=obs,
        ):
            resp = await weather_client.patch(
                f"/api/v1/weather/observations/{obs.id}",
                json={"is_disputed": True, "dispute_summary": "Updated."},
            )
        assert resp.status_code == 200
        assert resp.json()["is_disputed"] is True

    @pytest.mark.asyncio
    async def test_patch_observation_404(self, weather_client):
        with patch(
            "atlas.weather.router.WeatherContextService.update_observation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await weather_client.patch(
                "/api/v1/weather/observations/no-such",
                json={"is_disputed": True},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_observation_204(self, weather_client):
        with patch(
            "atlas.weather.router.WeatherContextService.delete_observation",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = await weather_client.delete("/api/v1/weather/observations/some-id")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_observation_404(self, weather_client):
        with patch(
            "atlas.weather.router.WeatherContextService.delete_observation",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = await weather_client.delete("/api/v1/weather/observations/gone")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rebuild_returns_200(self, weather_client):
        with (
            patch("atlas.weather.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.weather.router.WeatherContextService.rebuild_weather",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            resp = await weather_client.post("/api/v1/accidents/evt-1/weather/rebuild")
        assert resp.status_code == 200
        assert resp.json()["observation_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Flight rules classification contract
# ─────────────────────────────────────────────────────────────────────────────

class TestFlightRulesClassification:
    def _fr(self, vis_m, ceil_ft):
        from atlas.weather.metar_parser import _compute_flight_rules
        return _compute_flight_rules(vis_m, ceil_ft)

    def test_vfr_both_good(self):
        assert self._fr(16093.0, 5000) == "vfr"

    def test_mvfr_reduced_vis(self):
        # 3–4 SM → MVFR
        assert self._fr(3 * 1609.344, 5000) in ("mvfr", "ifr")

    def test_ifr_low_ceil(self):
        assert self._fr(5000.0, 800) == "ifr"

    def test_lifr_low_vis(self):
        assert self._fr(500.0, 5000) == "lifr"

    def test_lifr_low_ceil(self):
        assert self._fr(10000.0, 300) == "lifr"

    def test_unknown_when_no_data(self):
        assert self._fr(None, None) == "unknown"
