"""
Repository Profile schema version.

Bump this whenever a field is added, removed, or its meaning changes.
Any RepositoryProfile persisted to storage/repository_profile/ carries
this version so a future reader knows how to interpret it (or knows it
needs migrating).
"""

REPOSITORY_PROFILE_SCHEMA_VERSION = "1.1.0"