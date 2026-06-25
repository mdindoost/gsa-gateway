"""ESPN-backed World Cup data providers (scoreboard-primary).

The provider seam: fetch + parse live World Cup data into the provider-agnostic
``NormMatch`` shape the MatchWatcher state machine consumes. Swapping the data source
(ESPN ↔ football-data ↔ future) never touches the state machine — same pattern as
``njit_search`` / Scholar ``default_fetch``.
"""
