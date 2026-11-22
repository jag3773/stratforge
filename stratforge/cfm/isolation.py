"""Hard wall: cfm/ must not import the spot host or the short book."""

FORBIDDEN = (
    "strategies",
    "ccxt_bridge",
    "risk_limits",
    "short",
    "paper_executor",
    "live_node",
    "research_replay",
    "live_executor",
    "live_reconcile",
    "signal_engine",
)
