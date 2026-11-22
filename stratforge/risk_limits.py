"""
US-compliance + risk limit enforcement (pre-trade SSOT).

Used by LiveExecutor / LIVE. Paper path may skip or soft-log.
Override via env or RiskLimits(...) from config JSON.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from stratforge.state import state_root

ROOT = state_root()
HALT_MODES = ("paper", "live")


def global_halt_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "data" / "TRADING_HALTED"


def halt_file_for_mode(mode: str, root: Path | None = None) -> Path:
    m = (mode or "").strip().lower()
    if m not in HALT_MODES:
        m = "paper"
    return (root or ROOT) / "data" / f"TRADING_HALTED_{m}"


def resolve_halt_path(raw: str | Path | None, root: Path | None = None) -> Path:
    """Resolve halt_file against repo root so cwd cannot miss the kill switch."""
    base = root or ROOT
    if raw is None or str(raw).strip() == "":
        return global_halt_path(base)
    p = Path(str(raw))
    if not p.is_absolute():
        p = base / p
    return p


DEFAULT_HALT_FILE = str(global_halt_path())
# MTM headroom so a full book of clips is not RISK_DENY'd after winners.
# Config max_total_notional_usd is a floor; contributed * this is the live cap.
TOTAL_NOTIONAL_HEADROOM = 1.25


def raise_total_notional_for_contributed(
    limits: RiskLimits | None, contributed_usd: float
) -> float | None:
    """Never shrink the book cap. 0 means percent-only (no dollar book cap)."""
    if limits is None:
        return None
    current = float(limits.max_total_notional_usd or 0.0)
    if current <= 0:
        return current
    target = float(contributed_usd) * TOTAL_NOTIONAL_HEADROOM
    if target > current + 1e-9:
        limits.max_total_notional_usd = target
    return float(limits.max_total_notional_usd)


def clip_target_usd(
    equity: float, max_alloc_pct: float, max_position_notional_usd: float = 0.0
) -> float:
    """Percent clip. Dollar cap binds only when > 0."""
    target = float(equity) * float(max_alloc_pct)
    cap = float(max_position_notional_usd or 0.0)
    if cap > 0:
        target = min(target, cap)
    return target


def effective_daily_loss_halt(
    limits: RiskLimits | None, day_start_equity: float | None = None
) -> float | None:
    """Tightest configured halt. None = no daily dollar halt."""
    if limits is None:
        return None
    candidates: list[float] = []
    usd = float(getattr(limits, "daily_loss_halt_usd", 0.0) or 0.0)
    if usd > 0:
        candidates.append(usd)
    pct = float(getattr(limits, "daily_loss_halt_pct", 0.0) or 0.0)
    if pct > 0 and day_start_equity is not None and float(day_start_equity) > 0:
        candidates.append(float(day_start_equity) * pct)
    if not candidates:
        return None
    return min(candidates)


@dataclass
class RiskLimits:
    """Hard pre-trade limits. Live/shadow orders DENIED if any check fails."""

    max_position_notional_usd: float = 0.0
    max_total_notional_usd: float = 0.0
    daily_loss_halt_usd: float = 0.0
    daily_loss_halt_pct: float = 0.10
    max_leverage: float = 1.0
    max_alloc_pct: float = 0.25
    max_concurrent: int = 8
    long_only: bool = True
    allowed_venues: tuple = ("COINBASE",)
    allowed_products: tuple = ("spot",)  # v1: spot long-only only
    banned_instruments: tuple = ()
    allowed_symbols: tuple | None = None  # None = any; else allowlist e.g. BTC, ETH
    risk_per_trade_pct: float = 0.01
    min_notional_usd: float = 1.0
    halt_file: str = DEFAULT_HALT_FILE
    halt_mode: str = ""  # paper|shadow|live — mode file + global panic


def normalize_allowed_symbols(raw) -> tuple[str, ...] | None:
    """None / missing / [] / blank => unrestricted. Non-empty => allowlist.

    live_production.json documents empty allowed_symbols as 'full universe'.
    A literal [] must not become tuple() (deny-all).
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        items = [s.strip().upper() for s in raw.split(",") if s.strip()]
    else:
        try:
            items = [str(s).strip().upper() for s in raw if str(s).strip()]
        except TypeError:
            return None
    return tuple(items) if items else None


def load_limits_from_env(base: RiskLimits | None = None) -> RiskLimits:
    b = base or RiskLimits()

    def f(name, default):
        v = os.getenv(name)
        return float(v) if v is not None else default

    def i(name, default):
        v = os.getenv(name)
        return int(v) if v is not None else default

    def bflag(name, default):
        v = os.getenv(name)
        if v is None:
            return default
        return v.lower() in ("1", "true", "yes", "on")

    allowed = os.getenv("RISK_ALLOWED_SYMBOLS")
    allowed_t = (
        normalize_allowed_symbols(allowed) if allowed is not None else b.allowed_symbols
    )

    halt_mode = (os.getenv("RISK_HALT_MODE", b.halt_mode) or "").strip().lower()
    halt_raw = os.getenv("RISK_HALT_FILE", b.halt_file)
    return RiskLimits(
        max_position_notional_usd=f("RISK_MAX_POS_NOTIONAL", b.max_position_notional_usd),
        max_total_notional_usd=f("RISK_MAX_TOTAL_NOTIONAL", b.max_total_notional_usd),
        daily_loss_halt_usd=f("RISK_DAILY_LOSS_HALT", b.daily_loss_halt_usd),
        daily_loss_halt_pct=f("RISK_DAILY_LOSS_HALT_PCT", b.daily_loss_halt_pct),
        max_leverage=f("RISK_MAX_LEVERAGE", b.max_leverage),
        max_alloc_pct=f("RISK_MAX_ALLOC_PCT", b.max_alloc_pct),
        max_concurrent=i("RISK_MAX_CONCURRENT", b.max_concurrent),
        long_only=bflag("RISK_LONG_ONLY", b.long_only),
        allowed_symbols=allowed_t,
        risk_per_trade_pct=f("RISK_PER_TRADE_PCT", b.risk_per_trade_pct),
        min_notional_usd=f("RISK_MIN_NOTIONAL", b.min_notional_usd),
        halt_file=str(resolve_halt_path(halt_raw)),
        halt_mode=halt_mode,
        allowed_products=b.allowed_products,
        allowed_venues=b.allowed_venues,
        banned_instruments=b.banned_instruments,
    )


def limits_from_config(cfg: dict) -> RiskLimits:
    """Build limits from live_production.json risk block + env overrides."""
    r = cfg.get("risk") or {}
    halt_mode = str(r.get("halt_mode") or cfg.get("halt_mode") or "").strip().lower()
    halt_raw = r.get("halt_file")
    if halt_raw:
        halt_file = str(resolve_halt_path(halt_raw))
    elif halt_mode:
        halt_file = str(halt_file_for_mode(halt_mode))
    else:
        halt_file = DEFAULT_HALT_FILE
    base = RiskLimits(
        max_position_notional_usd=float(r.get("max_position_notional_usd", 0.0) or 0.0),
        max_total_notional_usd=float(r.get("max_total_notional_usd", 0.0) or 0.0),
        daily_loss_halt_usd=float(r.get("daily_loss_halt_usd", 0.0) or 0.0),
        daily_loss_halt_pct=float(r.get("daily_loss_halt_pct", 0.10) or 0.0),
        max_leverage=float(r.get("max_leverage", 1.0)),
        max_alloc_pct=float(r.get("max_alloc_pct", cfg.get("max_alloc_pct", 0.25))),
        max_concurrent=int(r.get("max_concurrent", cfg.get("max_concurrent", 8))),
        long_only=bool(r.get("long_only", cfg.get("long_only", True))),
        allowed_symbols=normalize_allowed_symbols(r.get("allowed_symbols")),
        risk_per_trade_pct=float(r.get("risk_per_trade_pct", cfg.get("risk_percentage", 0.01))),
        min_notional_usd=float(r.get("min_notional_usd", 1.0)),
        halt_file=halt_file,
        halt_mode=halt_mode,
        allowed_products=tuple(r.get("allowed_products", ("spot",))),
    )
    return load_limits_from_env(base)


class RiskCheckResult:
    def __init__(self):
        self.approved = True
        self.reasons: list[str] = []

    def deny(self, reason: str):
        self.approved = False
        self.reasons.append(reason)

    def note(self, reason: str):
        self.reasons.append(reason)

    def __str__(self):
        state = "APPROVED" if self.approved else "DENIED"
        return f"[{state}] " + ("; ".join(self.reasons) if self.reasons else "all checks passed")


def is_trading_halted(limits: RiskLimits | None = None, mode: str | None = None) -> tuple[bool, str]:
    """Env TRADING_HALTED, global panic file, mode file, or limits.halt_file."""
    limits = limits or load_limits_from_env()
    if os.getenv("TRADING_HALTED", "false").lower() in ("1", "true", "yes", "on"):
        return True, "TRADING_HALTED env set"

    global_path = global_halt_path()
    if global_path.is_file():
        return True, f"global halt file present: {global_path}"

    m = (mode or getattr(limits, "halt_mode", "") or "").strip().lower()
    if m:
        mode_path = halt_file_for_mode(m)
        if mode_path.is_file():
            return True, f"halt file present: {mode_path}"

    extra = resolve_halt_path(limits.halt_file)
    if extra != global_path and extra.is_file():
        return True, f"halt file present: {extra}"
    return False, ""


def check_order(
    symbol: str,
    side: str,  # buy / sell
    notional_usd: float,
    leverage: float = 1.0,
    product: str = "spot",
    current_total_notional: float = 0.0,
    daily_loss_usd: float = 0.0,
    open_positions: int = 0,
    equity: float | None = None,
    is_close: bool = False,
    limits: RiskLimits | None = None,
    day_start_equity: float | None = None,
) -> RiskCheckResult:
    """
    Pre-trade gate. Closes (is_close=True) still check halt + product, but skip
    notional expansion / concurrent caps so exits can flatten risk.
    """
    limits = limits or load_limits_from_env()
    res = RiskCheckResult()
    sym = symbol.replace("/", "-").upper()
    if sym.endswith("-USD"):
        base = sym[: -4]
    else:
        base = sym.split("-")[0]

    halted, why = is_trading_halted(limits)
    if halted and not is_close:
        res.deny(why)
        return res
    if halted and is_close:
        res.note(f"{why} (close allowed to flatten)")

    if product not in limits.allowed_products:
        res.deny(f"product '{product}' not allowed (allowed: {limits.allowed_products})")

    allow = normalize_allowed_symbols(limits.allowed_symbols)
    if allow is not None and base not in allow and sym not in allow:
        res.deny(f"symbol {base} not in allowlist {allow}")

    if sym in limits.banned_instruments or base in limits.banned_instruments:
        res.deny(f"instrument {symbol} is restricted")

    if leverage > limits.max_leverage:
        res.deny(f"leverage {leverage} exceeds cap {limits.max_leverage}")

    # v1 long-only: sell only allowed as close
    if limits.long_only and side.lower() == "sell" and not is_close:
        res.deny("long_only: opening shorts denied")

    if is_close:
        if res.approved:
            res.note("close allowed through risk gate")
        return res

    if notional_usd < limits.min_notional_usd:
        res.deny(f"notional ${notional_usd:.2f} below min ${limits.min_notional_usd:.2f}")

    if float(limits.max_position_notional_usd or 0) > 0:
        if notional_usd > limits.max_position_notional_usd:
            res.deny(
                f"notional ${notional_usd:,.2f} exceeds cap ${limits.max_position_notional_usd:,.2f}"
            )

    if float(limits.max_total_notional_usd or 0) > 0:
        if current_total_notional + notional_usd > limits.max_total_notional_usd + 1e-9:
            res.deny(f"total notional would exceed ${limits.max_total_notional_usd:,.2f}")

    if equity is not None and equity > 0 and limits.max_alloc_pct > 0:
        if notional_usd > equity * limits.max_alloc_pct + 1e-6:
            res.deny(
                f"notional ${notional_usd:.2f} exceeds max_alloc_pct "
                f"{limits.max_alloc_pct:.0%} of equity ${equity:.2f}"
            )

    if open_positions >= limits.max_concurrent and side.lower() == "buy":
        res.deny(f"open positions {open_positions} at max_concurrent {limits.max_concurrent}")

    halt_usd = effective_daily_loss_halt(limits, day_start_equity)
    if halt_usd is not None and daily_loss_usd >= halt_usd:
        res.deny(
            f"daily loss ${daily_loss_usd:,.2f} hit halt ${halt_usd:,.2f}"
        )

    if res.approved:
        res.note("pre-trade risk check passed")
    return res


if __name__ == "__main__":
    print(check_order("BTC-USD", "buy", notional_usd=100.0, equity=1000.0))
    print(check_order("BTC-USD", "buy", notional_usd=999.0, equity=1000.0))
    print(check_order("BTC-USD", "sell", notional_usd=100.0, is_close=False))
    print(check_order("BTC-USD", "sell", notional_usd=100.0, is_close=True))
