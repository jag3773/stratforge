"""
ccxt execution bridge for Coinbase Advanced Trade (US).

DRY-RUN by default. Live requires ALL of:
  LIVE_TRADING=true
  SIMULATION_MODE=false
  approval file (default data/LIVE_APPROVED) — no env bypass
  COINBASE_API_KEY + COINBASE_API_SECRET
  COINBASE_PORTFOLIO_ID (funded sleeve; no silent default on LIVE)

v1 production path: spot long-only (buy to open, sell to close). Shorts/CFM
remain available as lower-level helpers but LiveExecutor will not call them.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("ccxt_bridge")

ROOT = Path(__file__).resolve().parent
DEFAULT_JOURNAL = ROOT / "data" / "execution_log_bridge.jsonl"
DEFAULT_APPROVAL = ROOT / "data" / "LIVE_APPROVED"


def normalize_pem_secret(raw: str | None) -> str:
    """Coinbase Advanced Trade secrets are PEMs. .env / systemd EnvironmentFile
    often store real newlines as the two-char sequence \\n. Reconcile already
    unescaped; the order path did not (2026-08-22: Invalid symbol 92 / native
    account-register timeout). Safe no-op if newlines are already real."""
    if not raw:
        return ""
    return str(raw).strip().strip('"').strip("'").replace("\\n", "\n")


def load_live_secrets() -> None:
    """Opt-in: load .env.live then .env. Paper/shadow must never call this."""
    for name in (".env.live", ".env"):
        env_path = ROOT / name
        if not env_path.is_file():
            continue
        try:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k.upper() in ("COINBASE_API_SECRET", "API_SECRET"):
                    v = normalize_pem_secret(v)
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception as e:
            logger.warning("dotenv load failed (%s): %s", name, e)


# Do NOT auto-load secrets on import — paper/shadow PIDs must stay keyless.


def is_live_enabled() -> bool:
    live = os.getenv("LIVE_TRADING", "false").lower() == "true"
    sim = os.getenv("SIMULATION_MODE", "true").lower() != "false"
    return live and not sim


def approval_ok() -> tuple[bool, str]:
    """LIVE always requires repo data/LIVE_APPROVED. Env cannot move or disable this."""
    path = DEFAULT_APPROVAL
    if path.is_file():
        return True, f"approval file {path}"
    return False, f"missing approval file {path}"


def bridge_config(journal_path: str | Path | None = None) -> dict:
    live = is_live_enabled()
    ok, reason = approval_ok() if live else (True, "dry-run")
    return {
        "dry_run": (not live) or (not ok),
        "live_requested": live,
        "approval_ok": ok,
        "approval_reason": reason,
        "api_key": os.getenv("COINBASE_API_KEY", ""),
        "api_secret": normalize_pem_secret(os.getenv("COINBASE_API_SECRET", "")),
        "use_cfm": os.getenv("USE_CFM_FUTURES", "false").lower() == "true",
        "leverage": float(os.getenv("COINBASE_LEVERAGE", "1")),
        "journal_path": str(journal_path or os.getenv("BRIDGE_JOURNAL", DEFAULT_JOURNAL)),
        "long_only_default": True,
        "portfolio_id": (os.getenv("COINBASE_PORTFOLIO_ID") or "").strip(),
    }


def _journal(path: str, entry: dict):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    entry = {**entry, "ts": datetime.now(timezone.utc).isoformat()}
    with open(path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    logger.info("JOURNAL %s", json.dumps(entry, default=str))


def get_client(cfg: dict | None = None):
    import ccxt

    cfg = cfg or bridge_config()
    if cfg["dry_run"]:
        return ccxt.coinbase({"enableRateLimit": True})
    if not (cfg["api_key"] and cfg["api_secret"]):
        raise RuntimeError("Live trading requested but COINBASE_API_KEY/SECRET missing")
    options: dict[str, Any] = {"fetchCurrencies": False}
    pid = (cfg.get("portfolio_id") or os.getenv("COINBASE_PORTFOLIO_ID") or "").strip()
    if pid:
        options["portfolio"] = pid
    return ccxt.coinbase(
        {
            "apiKey": cfg["api_key"],
            "secret": cfg["api_secret"],
            "enableRateLimit": True,
            "options": options,
        }
    )


def live_order_params(
    client_order_id: str,
    cfg: dict | None = None,
    order_type: str = "market",
) -> dict[str, Any]:
    params: dict[str, Any] = {"clientOrderId": client_order_id}
    pid = ""
    if cfg:
        pid = str(cfg.get("portfolio_id") or "").strip()
    pid = pid or (os.getenv("COINBASE_PORTFOLIO_ID") or "").strip()
    if pid:
        params["retail_portfolio_id"] = pid
    if str(order_type).lower() == "limit":
        params["timeInForce"] = "IOC"
    return params


def resolved_fill(order: dict | None) -> float:
    """Venue-reported fill only. Never fall back to requested size."""
    if not isinstance(order, dict):
        return 0.0
    raw = order.get("filled")
    if raw is None or raw == "":
        return 0.0
    try:
        qty = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return qty if qty > 0 else 0.0


def _fnum(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resolved_avg(order: dict | None) -> float | None:
    """Venue average only. Do not use request/limit price."""
    if not isinstance(order, dict):
        return None
    return _fnum(order.get("average"))


def resolved_cost(order: dict | None) -> float | None:
    """Venue quote cost only. Do not use requested qty × ref price."""
    if not isinstance(order, dict):
        return None
    return _fnum(order.get("cost"))


def resolved_fee(order: dict | None) -> tuple[float | None, str | None]:
    """USD fee from venue. Never invent a rate."""
    if not isinstance(order, dict):
        return None, None
    blobs: list = []
    fee = order.get("fee")
    if isinstance(fee, dict):
        blobs.append(fee)
    fees = order.get("fees")
    if isinstance(fees, list):
        blobs.extend(x for x in fees if isinstance(x, dict))
    for blob in blobs:
        ccy = str(blob.get("currency") or blob.get("code") or "").upper()
        amt = _fnum(blob.get("cost"))
        if amt is None:
            continue
        if ccy and ccy not in {"USD", "USDC", "USDT"}:
            continue
        return amt, (ccy or "USD")
    return None, None


def normalize_ccxt_order(order: dict | None) -> dict:
    """Collapse a ccxt order into journal fields. complete=False if economics missing."""
    filled = resolved_fill(order)
    avg = resolved_avg(order)
    cost = resolved_cost(order)
    fee, fee_ccy = resolved_fee(order)
    if cost is None and filled > 0 and avg is not None:
        cost = filled * avg
    if avg is None and cost is not None and filled > 0:
        avg = cost / filled
    status = ""
    oid = None
    if isinstance(order, dict):
        status = str(order.get("status") or "")
        oid = order.get("id")
    complete = filled > 0 and cost is not None and fee is not None and avg is not None
    return {
        "filled": filled,
        "avg_price": avg,
        "cost": cost,
        "fee": fee,
        "fee_currency": fee_ccy,
        "status": status or None,
        "order_id": oid,
        "complete": complete,
    }


def to_ccxt_symbol(symbol: str) -> str:
    s = symbol.replace("-", "/").upper()
    if "/" not in s:
        s = f"{s}/USD"
    return s


def to_venue_symbol(symbol: str) -> str:
    """Coinbase product id: BTC/USD → BTC-USD."""
    return symbol.replace("/", "-").upper()


def place_order(
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "market",
    price: float | None = None,
    client_order_id: str | None = None,
    reduce_only: bool = False,
    journal_path: str | Path | None = None,
    cfg: dict | None = None,
) -> dict[str, Any]:
    """
    Place order. side buy|sell. Returns normalized fill dict.
    dry-run: no exchange call.
    """
    cfg = cfg or bridge_config(journal_path)
    jpath = str(journal_path or cfg["journal_path"])
    ccxt_symbol = to_ccxt_symbol(symbol)
    venue_sym = to_venue_symbol(symbol)
    coid = client_order_id or f"nt-{uuid.uuid4().hex[:16]}"
    side_l = side.lower()

    base_entry = {
        "symbol": venue_sym,
        "ccxt_symbol": ccxt_symbol,
        "side": side_l,
        "quantity": float(quantity),
        "order_type": order_type,
        "client_order_id": coid,
        "reduce_only": reduce_only,
        "ref_price": price,
    }

    if cfg["dry_run"]:
        entry = {
            **base_entry,
            "action": "DRY_RUN_ORDER",
            "status": "SIMULATED",
            "venue": "COINBASE_SPOT",
            "avg_price": price,
            "filled": float(quantity),
            "fee": None,
            "live_requested": cfg.get("live_requested", False),
            "approval_reason": cfg.get("approval_reason"),
        }
        _journal(jpath, entry)
        logger.warning(
            "[DRY-RUN] would %s %s %s coid=%s", side_l, quantity, venue_sym, coid
        )
        return entry

    if not cfg.get("approval_ok", False):
        raise RuntimeError(f"Live blocked: {cfg.get('approval_reason')}")

    if str(order_type).lower() == "limit":
        try:
            px = float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            px = 0.0
        if not (math.isfinite(px) and px > 0):
            raise ValueError("limit order requires price > 0")

    client = get_client(cfg)
    params: dict[str, Any] = live_order_params(coid, cfg, order_type=order_type)
    try:
        order = client.create_order(
            ccxt_symbol, order_type, side_l, float(quantity), price, params
        )
        norm = normalize_ccxt_order(order)
        if not norm["complete"]:
            oid = order.get("id") if isinstance(order, dict) else None
            for _ in range(8):
                if not oid:
                    break
                time.sleep(0.25)
                try:
                    order = client.fetch_order(oid, ccxt_symbol)
                except Exception:
                    continue
                norm = normalize_ccxt_order(order)
                if norm["complete"]:
                    break
        entry = {
            **base_entry,
            "action": "LIVE_ORDER",
            "status": norm["status"],
            "order_id": norm["order_id"] or (order.get("id") if isinstance(order, dict) else None),
            "filled": norm["filled"],
            "avg_price": norm["avg_price"],
            "cost": norm["cost"],
            "fee": norm["fee"],
            "fee_currency": norm["fee_currency"],
            "complete": norm["complete"],
        }
        _journal(jpath, entry)
        return entry
    except Exception as e:
        _journal(
            jpath,
            {**base_entry, "action": "LIVE_ORDER_FAILED", "error": str(e)},
        )
        logger.error("Order failed: %s", e)
        raise


def query_order(
    symbol: str,
    client_order_id: str,
    journal_path: str | Path | None = None,
    cfg: dict | None = None,
) -> dict[str, Any] | None:
    """Look up an order by client_order_id. Dry-run returns None. Never places orders."""
    cfg = cfg or bridge_config(journal_path)
    if cfg["dry_run"]:
        return None
    if not cfg.get("approval_ok", False):
        return None
    client = get_client(cfg)
    ccxt_symbol = to_ccxt_symbol(symbol)
    order = None
    try:
        order = client.fetch_order(client_order_id, ccxt_symbol, {"clientOrderId": client_order_id})
    except Exception:
        try:
            order = client.fetch_order(None, ccxt_symbol, {"clientOrderId": client_order_id})
        except Exception as e:
            logger.warning("query_order %s %s failed: %s", client_order_id, ccxt_symbol, e)
            return None
    if not order:
        return None
    norm = normalize_ccxt_order(order)
    status = str(norm["status"] or "unknown").lower()
    return {
        "action": "LIVE_ORDER_QUERY",
        "status": status,
        "symbol": to_venue_symbol(symbol),
        "side": (order.get("side") or "").lower(),
        "quantity": float(order.get("amount") or 0.0),
        "filled": norm["filled"],
        "avg_price": norm["avg_price"],
        "cost": norm["cost"],
        "fee": norm["fee"],
        "fee_currency": norm["fee_currency"],
        "complete": norm["complete"],
        "order_id": norm["order_id"] or order.get("id"),
        "client_order_id": client_order_id,
        "open": status in ("open", "pending", "new", "active"),
    }


def cancel_order(
    symbol: str,
    client_order_id: str,
    cfg: dict | None = None,
) -> bool:
    cfg = cfg or bridge_config()
    if cfg["dry_run"]:
        return False
    client = get_client(cfg)
    try:
        client.cancel_order(client_order_id, to_ccxt_symbol(symbol), {"clientOrderId": client_order_id})
        return True
    except Exception as e:
        logger.warning("cancel_order %s failed: %s", client_order_id, e)
        return False


def fetch_l1(
    symbol: str,
    cfg: dict | None = None,
    journal_path: str | Path | None = None,
) -> dict[str, float]:
    """Best bid/ask/mid from fetch_ticker. Never journals. Raises if L1 missing."""
    cfg = cfg or bridge_config(journal_path)
    client = get_client(cfg)
    ccxt_symbol = to_ccxt_symbol(symbol)
    ticker = client.fetch_ticker(ccxt_symbol)
    bid = _fnum(ticker.get("bid") if isinstance(ticker, dict) else None)
    ask = _fnum(ticker.get("ask") if isinstance(ticker, dict) else None)
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        raise ValueError(f"missing bid/ask for {ccxt_symbol}")
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}


def limit_buy_ioc(symbol: str, quantity: float, price: float, **kw) -> dict:
    return place_order(symbol, "buy", quantity, order_type="limit", price=price, **kw)


def market_buy(symbol: str, quantity: float, **kw) -> dict:
    return place_order(symbol, "buy", quantity, order_type="market", **kw)


def market_sell(symbol: str, quantity: float, reduce_only: bool = True, **kw) -> dict:
    return place_order(
        symbol, "sell", quantity, order_type="market", reduce_only=reduce_only, **kw
    )


def close_long(symbol: str, quantity: float, **kw) -> dict:
    return market_sell(symbol, quantity, reduce_only=True, **kw)


def get_balance(cfg: dict | None = None) -> dict:
    cfg = cfg or bridge_config()
    if cfg["dry_run"]:
        return {"USD": {"free": 0.0, "total": 0.0}, "note": "DRY-RUN — no balance fetch"}
    client = get_client(cfg)
    return client.fetch_balance()


def status(cfg: dict | None = None) -> dict:
    cfg = cfg or bridge_config()
    return {
        "dry_run": cfg["dry_run"],
        "live_requested": cfg.get("live_requested"),
        "approval_ok": cfg.get("approval_ok"),
        "approval_reason": cfg.get("approval_reason"),
        "venue": "COINBASE_SPOT",
        "leverage": cfg["leverage"],
        "live_trading_enabled": not cfg["dry_run"],
        "has_keys": bool(cfg.get("api_key") and cfg.get("api_secret")),
    }


# Back-compat module-level CONFIG snapshot
CONFIG = bridge_config()


if __name__ == "__main__":
    print("ccxt bridge status:", status())
    print(place_order("BTC-USD", "buy", 0.001, price=60000.0))
    print(place_order("BTC-USD", "sell", 0.001, price=60000.0, reduce_only=True))
