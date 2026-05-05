"""
NTSB ingestion adapter — fetch, hash, snapshot.
Does NOT normalize or write claims.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import uuid
from datetime import date
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from atlas.config import get_settings
from atlas.models.orm import RawSnapshot

log = structlog.get_logger(__name__)
settings = get_settings()

NTSB_SOURCE_ID = "src-ntsb-001"
NTSB_QUERY_URL = f"{settings.ntsb_api_base}/Query/Main"


class NTSBFetchError(Exception):
    pass


class NTSBAdapter:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._run_id = str(uuid.uuid4())

    async def __aenter__(self) -> NTSBAdapter:
        self._client = httpx.AsyncClient(
            timeout=settings.ntsb_timeout_s,
            headers={"User-Agent": "AviationSafetyAtlas/0.1 (research)"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(settings.ntsb_max_retries),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self._client
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def fetch_date_range(self, start: date, end: date) -> list[dict[str, Any]]:
        all_records: list[dict[str, Any]] = []
        offset = 0
        batch = settings.ntsb_batch_size

        while True:
            try:
                data = await self._get(NTSB_QUERY_URL, {
                    "StartDate": start.isoformat(),
                    "EndDate": end.isoformat(),
                    "offset": offset,
                    "rows": batch,
                })
            except httpx.HTTPStatusError as exc:
                raise NTSBFetchError(f"NTSB API {exc.response.status_code}") from exc

            records = data.get("accidents") or data.get("results") or []
            if not records:
                break
            all_records.extend(records)
            log.info("ntsb.page", offset=offset, count=len(records))
            if len(records) < batch:
                break
            offset += batch
            await asyncio.sleep(settings.ntsb_request_delay_s)

        log.info("ntsb.done", total=len(all_records))
        return all_records


def compute_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_snapshot(
    raw: dict[str, Any],
    source_record_id: str | None = None,
    run_id: str | None = None,
) -> RawSnapshot:
    event_id = source_record_id or raw.get("EventId") or ""
    # NOTE: source_url is left None here — it is only set after URL verification
    # (check-links command). Never fabricate provenance URLs.
    return RawSnapshot(
        id=str(uuid.uuid4()),
        source_id=NTSB_SOURCE_ID,
        source_record_id=event_id or None,
        payload=raw,
        payload_hash=compute_payload_hash(raw),
        source_url=None,  # set after link verification
        ingestion_run_id=run_id,
    )


async def load_from_csv(filepath: str) -> list[dict[str, Any]]:
    CSV_TO_API = {
        "Event.Id": "EventId", "Event.Date": "EventDate", "Event.Time": "EventTime",
        "Location.City.Name": "City", "Location.State.Name": "State",
        "Location.Country.Name": "Country",
        "Location.Latitude": "LatDecimal", "Location.Longitude": "LongDecimal",
        "Aircraft.Aircraft.Damage": "AircraftDamage", "Injury.Highest.Injury": "HighestInjury",
        "Injury.Total.Fatal.Injuries": "TotalFatalInjuries",
        "Injury.Total.Serious.Injuries": "TotalSeriousInjuries",
        "Injury.Total.Minor.Injuries": "TotalMinorInjuries",
        "Aircraft.Make": "Make", "Aircraft.Model": "Model",
        "Aircraft.Registration.Number": "Registration",
        "Aircraft.Amateur.Built": "AmateurBuilt",
        "Aircraft.Engine.Type": "EngineType",
        "Aircraft.Number.of.Engines": "NumberOfEngines",
        "Operator.Operator.Name": "OperatorName",
        "Flight.Purpose.of.Flight": "PurposeOfFlight",
        "Flight.Broad.Phase.of.Flight": "PhaseOfFlight",
        "Weather.Sky.Condition": "WeatherCondition",
        "Investigation.Type": "InvestigationType",
        "Narrative.Probable.Cause": "ProbableCause",
    }

    def _read() -> list[dict[str, Any]]:
        records = []
        with open(filepath, newline="", encoding="latin-1") as f:
            for row in csv.DictReader(f):
                records.append({CSV_TO_API.get(k, k): (v or None) for k, v in row.items()})
        return records

    # asyncio.to_thread is the documented replacement for the deprecated
    # get_event_loop().run_in_executor(None, ...) pattern.  Same semantics
    # (offload sync work to the default thread pool), no DeprecationWarning,
    # and it does the right thing whether or not we are inside a running loop.
    return await asyncio.to_thread(_read)
