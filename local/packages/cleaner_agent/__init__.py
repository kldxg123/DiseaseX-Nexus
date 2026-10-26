"""Local rule-based cleaner agent MVP."""

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.date_normalization import iso_week_from_date, parse_event_date
from cleaner_agent.disease_detection import best_disease_match, detect_disease_mentions
from cleaner_agent.event_extraction import (
    ExtractedEvent,
    extract_event_from_raw_document,
    extract_numbers_for_epidemic_events,
)
from cleaner_agent.event_store import EpidemicEventStore
from cleaner_agent.feature_engineering import (
    WeeklyAggregate,
    aggregate_events_weekly,
    build_features_from_weekly_aggregates,
)
from cleaner_agent.feature_store import ModelFeatureStore
from cleaner_agent.features import FeatureBuilder
from cleaner_agent.geo_detection import best_country_match, detect_country_mentions, detect_setting
from cleaner_agent.language import detect_language
from cleaner_agent.quality import compute_event_quality_score
from cleaner_agent.text_cleaning import clean_text

__version__ = "0.1.0"

__all__ = [
    "CleanerAgent",
    "EpidemicEventStore",
    "ExtractedEvent",
    "FeatureBuilder",
    "ModelFeatureStore",
    "WeeklyAggregate",
    "aggregate_events_weekly",
    "best_country_match",
    "best_disease_match",
    "build_features_from_weekly_aggregates",
    "clean_text",
    "compute_event_quality_score",
    "detect_country_mentions",
    "detect_disease_mentions",
    "detect_language",
    "detect_setting",
    "extract_event_from_raw_document",
    "extract_numbers_for_epidemic_events",
    "iso_week_from_date",
    "parse_event_date",
]
