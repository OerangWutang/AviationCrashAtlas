"""
weather_timeline.py — Helpers for integrating weather context with the
Accident Timeline Reconstruction feature.

Principles
----------
- Weather observations appear in the timeline only when:
    1. A source/claim explicitly associates the weather with a flight phase, OR
    2. A reviewer manually creates a weather-type timeline event.
- METARs are NEVER automatically promoted to causal timeline events.
  A METAR showing low visibility near the accident time is contextual evidence,
  not proof that visibility caused the accident.

Usage pattern
-------------
  from atlas.weather.weather_timeline import weather_event_types, suggest_timeline_event

  # After creating a weather observation, the reviewer can optionally create a
  # corresponding timeline event via the standard timeline API.
  event_type = suggest_timeline_event(obs)
  if event_type and reviewer_consents:
      await TimelineReconstructionService.create_event(
          db,
          accident_event_id=obs.accident_event_id,
          event_type=event_type,
          title=...,
          category="in_flight",
          ...
      )

Weather-specific timeline event_type values
-------------------------------------------
These extend the core event_type vocabulary and are valid values for
AccidentTimelineEvent.event_type when weather context is the subject.
"""
from __future__ import annotations

from atlas.models.orm import AccidentWeatherObservation, FlightRules

# Weather event types that can be used in AccidentTimelineEvent.event_type
WEATHER_EVENT_TYPES: frozenset[str] = frozenset({
    "weather_deteriorated",
    "visibility_reduced",
    "thunderstorm_encountered",
    "icing_conditions_reported",
    "wind_shear_suspected",
    "turbulence_encountered",
    "low_ceiling_ifr_conditions",
    "weather_briefing_available",
    "weather_advisory_issued",
})


def suggest_timeline_event_type(obs: AccidentWeatherObservation) -> str | None:
    """
    Suggest a weather-specific timeline event type for an observation.

    Returns None if no specific weather event is strongly implied.
    The suggestion is advisory only — reviewers decide whether to create
    the timeline event and whether it represents a contributing factor.

    Does NOT assert causation. The returned string is just a vocabulary
    hint for the reviewer's event_type field.
    """
    if obs.thunderstorm_present:
        return "thunderstorm_encountered"

    if obs.icing_risk in ("likely", "severe"):
        return "icing_conditions_reported"

    if obs.turbulence_risk in ("likely", "severe"):
        return "turbulence_encountered"

    fr = obs.flight_rules
    if fr in (FlightRules.LIFR, FlightRules.IFR):
        return "low_ceiling_ifr_conditions"

    if fr == FlightRules.MVFR:
        return "visibility_reduced"

    return None


def weather_observation_category() -> str:
    """Return the canonical timeline category for a weather-linked event."""
    return "in_flight"
