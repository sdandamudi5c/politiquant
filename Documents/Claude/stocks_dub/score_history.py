"""
Score history tracker and self-learning prediction engine.

Workflow:
  1. Growth Report saves scored stocks each run → score_history.json
  2. Daily: update_outcomes() checks records older than 30 days, fetches actual price
  3. After enough outcomes: train_model() fits ridge regression on factor → return data
  4. predict_return() uses the trained model to estimate future 30-day return
"""

import json
import os
import uuid
from datetime import datetime, timedelta

import numpy as np

from scorer import FACTOR_NAMES

HISTORY_FILE  = os.path.join(os.path.dirname(__file__), "score_history.json")
OUTCOME_DAYS  = 30   # days after scoring to measure actual return
MIN_TRAIN     = 20   # minimum outcomes before model activates
RIDGE_LAMBDA  = 2.0  # regularisation — higher = more conservative weights


# ── Persistence ───────────────────────────────────────────────────────────────

def _load() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": 1, "records": [], "model": None}


def _save(data: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Writing scores ────────────────────────────────────────────────────────────

def save_scores(scored_stocks: "list[dict]"):
    """
    Call after each Growth Report run.
    scored_stocks: list of {ticker, price, score, factor_pts}
      factor_pts: dict mapping FACTOR_NAMES → point contribution (from score_stock_detailed)
    Skips tickers already recorded today to avoid duplicates.
    """
    data  = _load()
    today = datetime.today().strftime("%Y-%m-%d")

    today_tickers = {
        r["ticker"] for r in data["records"] if r["scored_date"] == today
    }

    added = 0
    for stock in scored_stocks:
        ticker = stock.get("ticker", "")
        if not ticker or ticker in today_tickers:
            continue
        data["records"].append({
            "id":               str(uuid.uuid4())[:8],
            "scored_date":      today,
            "ticker":           ticker,
            "price_at_score":   stock.get("price"),
            "total_score":      stock.get("score"),
            "factor_pts":       stock.get("factor_pts", {}),
            "outcome_date":     None,
            "price_at_outcome": None,
            "actual_return_pct": None,
        })
        added += 1

    if added:
        _save(data)
    return added


# ── Resolving outcomes ────────────────────────────────────────────────────────

def _fetch_price_on_date(ticker: str, target_date: "datetime") -> "float | None":
    """
    Fetch the closing price on or just after target_date using yfinance history.
    This means outcomes are always measured at exactly day-30, regardless of
    when the app is opened — laptop can be off for weeks without corrupting data.
    """
    try:
        import yfinance as yf
        start = target_date
        end   = target_date + timedelta(days=5)  # buffer for weekends/holidays
        hist  = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"),
                                          end=end.strftime("%Y-%m-%d"),
                                          auto_adjust=True)
        if not hist.empty:
            return float(hist["Close"].iloc[0])
    except Exception:
        pass
    return None


def update_outcomes() -> int:
    """
    For records where scored_date + 30 days has passed, fetch the closing price
    on exactly day 30 (from yfinance historical data) and compute the actual return.
    Works correctly even if the app hasn't been opened for weeks — the outcome
    date is fixed at scored_date + 30 days, not "whenever you open the app".
    Returns count of newly resolved outcomes.
    Trains model automatically when new outcomes bring total past MIN_TRAIN.
    """
    try:
        import yfinance as yf  # noqa: F401 — verify import works
    except ImportError:
        return 0

    data    = _load()
    today   = datetime.today()
    pending = [
        r for r in data["records"]
        if r["actual_return_pct"] is None
        and r["price_at_score"] is not None
        and (today - datetime.strptime(r["scored_date"], "%Y-%m-%d")).days >= OUTCOME_DAYS
    ]

    if not pending:
        return 0

    updated = 0
    for r in pending:
        scored_dt  = datetime.strptime(r["scored_date"], "%Y-%m-%d")
        target_dt  = scored_dt + timedelta(days=OUTCOME_DAYS)
        p = _fetch_price_on_date(r["ticker"], target_dt)
        if p is None:
            continue
        ret = (p - r["price_at_score"]) / r["price_at_score"] * 100
        r["outcome_date"]      = target_dt.strftime("%Y-%m-%d")  # fixed day-30 date
        r["price_at_outcome"]  = round(p, 4)
        r["actual_return_pct"] = round(ret, 4)
        updated += 1

    if updated:
        _save(data)
        # Retrain whenever we get new outcomes
        completed = sum(1 for r in data["records"] if r["actual_return_pct"] is not None)
        if completed >= MIN_TRAIN:
            train_model()

    return updated


# ── Model training ────────────────────────────────────────────────────────────

def train_model() -> "dict | None":
    """
    Ridge regression: factor_pts → actual_30d_return.
    Learns which factors genuinely predicted returns in your data.
    Returns model dict (also persisted to history file).
    """
    data      = _load()
    completed = [
        r for r in data["records"]
        if r["actual_return_pct"] is not None and r.get("factor_pts")
    ]

    if len(completed) < MIN_TRAIN:
        return None

    X_rows, y_vals = [], []
    for r in completed:
        fp  = r["factor_pts"]
        row = [fp.get(name, 0.0) for name in FACTOR_NAMES]
        X_rows.append(row)
        y_vals.append(r["actual_return_pct"])

    X = np.array(X_rows, dtype=float)
    y = np.array(y_vals, dtype=float)

    # Add bias column
    X_b = np.hstack([X, np.ones((len(X), 1))])

    # Ridge: minimise ||Xw - y||² + λ||w||²
    n_feat = X_b.shape[1]
    reg    = np.eye(n_feat)
    reg[-1, -1] = 0  # don't regularise bias
    params = np.linalg.solve(X_b.T @ X_b + RIDGE_LAMBDA * reg, X_b.T @ y)

    weights   = params[:-1].tolist()
    bias      = float(params[-1])
    y_pred    = X_b @ params
    residuals = y - y_pred
    ss_res    = float(np.sum(residuals ** 2))
    ss_tot    = float(np.sum((y - np.mean(y)) ** 2))
    r2        = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    resid_std = float(np.std(residuals))

    # Per-score-tier accuracy (for performance page)
    tier_stats = _compute_tier_stats(completed)

    model = {
        "weights":      weights,
        "bias":         bias,
        "factor_names": FACTOR_NAMES,
        "n_training":   len(completed),
        "r_squared":    round(r2, 4),
        "residual_std": round(resid_std, 4),
        "mean_return":  round(float(np.mean(y)), 4),
        "last_trained": datetime.today().strftime("%Y-%m-%d"),
        "tier_stats":   tier_stats,
    }

    data["model"] = model
    _save(data)
    return model


def _compute_tier_stats(completed: list) -> dict:
    tiers = {"STRONG BUY": [], "BUY": [], "WATCH": [], "NEUTRAL": [], "AVOID": []}
    for r in completed:
        s = r.get("total_score") or 0
        if   s >= 65: t = "STRONG BUY"
        elif s >= 50: t = "BUY"
        elif s >= 35: t = "WATCH"
        elif s >= 20: t = "NEUTRAL"
        else:         t = "AVOID"
        tiers[t].append(r["actual_return_pct"])

    stats = {}
    for tier, rets in tiers.items():
        if rets:
            stats[tier] = {
                "count":      len(rets),
                "avg_return": round(sum(rets) / len(rets), 2),
                "win_rate":   round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                "best":       round(max(rets), 2),
                "worst":      round(min(rets), 2),
            }
    return stats


# ── Prediction ────────────────────────────────────────────────────────────────

def predict_return(factor_pts: dict) -> "tuple[float, float, int, float] | None":
    """
    Predict 30-day return using the trained model.
    Returns (predicted_return_pct, ±ci_pct_95, n_training, r_squared)
    or None if model not ready yet.
    """
    data  = _load()
    model = data.get("model")
    if not model or model["n_training"] < MIN_TRAIN:
        return None

    weights      = np.array(model["weights"])
    factor_names = model["factor_names"]
    row          = np.array([factor_pts.get(name, 0.0) for name in factor_names])
    predicted    = float(row @ weights) + model["bias"]
    ci_95        = model["residual_std"] * 1.96  # 95% confidence interval (±)

    return (
        round(predicted, 2),
        round(ci_95, 2),
        model["n_training"],
        model["r_squared"],
    )


# ── Performance stats (for Model Performance page) ───────────────────────────

def get_stats() -> dict:
    """Full stats dict for the Model Performance page."""
    data      = _load()
    records   = data["records"]
    completed = [r for r in records if r["actual_return_pct"] is not None]
    pending   = [r for r in records if r["actual_return_pct"] is None]
    model     = data.get("model")

    factor_importance = []
    if model:
        weights = model["weights"]
        names   = model["factor_names"]
        factor_importance = sorted(
            [{"factor": n, "weight": round(w, 4), "abs": round(abs(w), 4)}
             for n, w in zip(names, weights)],
            key=lambda x: x["abs"], reverse=True,
        )

    recent_outcomes = sorted(
        completed, key=lambda r: r.get("outcome_date") or "", reverse=True
    )[:30]

    return {
        "n_total":            len(records),
        "n_completed":        len(completed),
        "n_pending":          len(pending),
        "min_train":          MIN_TRAIN,
        "model":              model,
        "factor_importance":  factor_importance,
        "recent_outcomes":    recent_outcomes,
        "needs_more":         max(0, MIN_TRAIN - len(completed)),
    }
