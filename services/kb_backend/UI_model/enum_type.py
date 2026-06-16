from enum import Enum

from basemodel.services_databaseconnector.postgres_model import (
    Language,
    SourceType,
    Tier,
    ActiveStatus as Status,
    PolicyFilteringType,
    PolicyExtractionType,
    ConflictType,
    ConflictResolution,
    ConflictSeverity,
    ConflictStatus,
    TaskType,
    SimilarityMetric,
    HttpMethod,
    APIType,
)

# Alias used in inventory models
DataLayerTier = Tier


class WarehouseType(Enum):
    snowflake  = "Snowflake"
    databricks = "Databricks"
