"""
Single source of truth for the graph schema version.

Bump this whenever node_types.py or edge_types.py changes shape
(new required field, renamed type, changed enum value). Every
persisted graph snapshot stores the version it was built with, so
the builder/query/analysis layers can detect and handle schema drift
instead of silently misreading old data.
"""

SCHEMA_VERSION = "1.0.0"