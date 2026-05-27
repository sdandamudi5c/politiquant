"""
Score history tracker and self-learning prediction engine.

Workflow:
  1. Growth Report saves scored stocks each run → score_history.json
  2. Daily: update_outcomes() checks records older than 30 days, fetches actual price
  3. After enough outcomes: train_model() fits Gradient Boosting on factor → return data
  4. predict_return() uses the trained model to estimate future 30-day return

Model upgrade (v2): Ridge regression → Gradient Boosting (sklearn)
  - Handles non-linear relationships between factors and returns
  - Feature importance shows which signals actually predict returns
  - Cross-validation score measures real out-of-sample accuracy
  - Trained model saved as ml_model.pkl alongside history JSON
"""

import json
import os
import uuid
from datetime import datetime, timedelta

import numpy as np

from scorer import FACTOR_NAMES

HISTORY_FILE  = os.path.join(os.path.dirname(__file__), "score_history.json")
MODEL_FILE    = os.path.join(os.path.dirname(__file__), "ml_model.pkl")
OUTCOME_DAYS  = 30   # days after scoring to measure actual return
MIN_TRAIN     = 20   # minimum outcomes before model activates
RIDGE_LAMBDA  = 2.0  # kept for fallback only


# ── Persistence ───────────────────────────────────────────────────────────────

_cache: dict = {}          # in-memory cache — invalidated on every _save()
_cache_mtime: float = -1   # file mtime when cache was last loaded


def _load() -> dict:
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else -1
    except OSError:
        mtime = -1
    if _cache and mtime == _cache_mtime:
        return _cache          # file unchanged — return cached copy
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                _cache = json.load(f)
            _cache_mtime = mtime
            return _cache
        except Exception:
            pass
    _cache = {"version": 1, "records": [], "model": None}
    _cache_mtime = -1
    return _cache


def _save(data: dict):
    global _cache, _cache_mtime
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)
    _cache = data
    try:
        _cache_mtime = os.path.getmtime(HISTORY_FILE)
    except OSError:
        _cache_mtime = -1


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
    Gradient Boosting: factor_pts → actual_30d_return.

    Improvements over old Ridge regression:
      - Non-linear: captures interaction effects between factors
      - Feature importance: shows which signals actually predict returns
      - Cross-validation: real out-of-sample accuracy estimate
      - Winsorisation: extreme outliers (±50%) capped to prevent distortion
      - Saved as ml_model.pkl for fast loading at prediction time
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
        # Use all current FACTOR_NAMES; older records missing new factors get 0
        row = [float(fp.get(name, 0.0) or 0.0) for name in FACTOR_NAMES]
        X_rows.append(row)
        y_vals.append(r["actual_return_pct"])

    X = np.array(X_rows, dtype=float)
    y = np.array(y_vals, dtype=float)

    # Winsorise targets: cap at ±50% to reduce outlier distortion
    y_clipped = np.clip(y, -50.0, 50.0)

    try:
        from sklearn.model_selection import cross_val_score, cross_val_predict
        from sklearn.preprocessing import StandardScaler
        import joblib

        # Scale features
        scaler   = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Try XGBoost first (better performance), fall back to Random Forest
        try:
            from xgboost import XGBRegressor
            model_obj = XGBRegressor(
                n_estimators     = 400,
                max_depth        = 4,
                learning_rate    = 0.05,
                subsample        = 0.8,
                colsample_bytree = 0.8,
                min_child_weight = 10,   # regularisation for small financial datasets
                reg_alpha        = 0.1,  # L1
                reg_lambda       = 1.0,  # L2
                random_state     = 42,
                n_jobs           = -1,
                verbosity        = 0,
            )
            _engine_name = "xgboost"
        except ImportError:
            from sklearn.ensemble import RandomForestRegressor
            model_obj = RandomForestRegressor(
                n_estimators     = 300,
                max_depth        = 4,
                min_samples_leaf = 20,
                random_state     = 42,
                n_jobs           = -1,
            )
            _engine_name = "random_forest"

        model_obj.fit(X_scaled, y_clipped)

        # 5-fold CV directional accuracy (more meaningful than R² for stocks)
        y_cv      = cross_val_predict(model_obj, X_scaled, y_clipped, cv=5)
        dir_acc   = float(np.mean(np.sign(y_cv) == np.sign(y_clipped)) * 100)
        baseline  = float(np.mean(y_clipped > 0) * 100)
        cv_scores = cross_val_score(model_obj, X_scaled, y_clipped, cv=5, scoring="r2")
        cv_r2     = float(np.mean(cv_scores))

        # In-sample metrics
        y_pred    = model_obj.predict(X_scaled)
        residuals = y_clipped - y_pred
        ss_res    = float(np.sum(residuals ** 2))
        ss_tot    = float(np.sum((y_clipped - np.mean(y_clipped)) ** 2))
        r2        = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        resid_std = float(np.std(y_cv - y_clipped))  # use CV residuals (more honest)

        # Feature importance — which factors actually predict returns
        importance = [
            {"factor": name, "importance": round(float(imp), 4)}
            for name, imp in zip(FACTOR_NAMES, model_obj.feature_importances_)
        ]
        importance.sort(key=lambda x: x["importance"], reverse=True)

        # Save model + scaler to disk
        joblib.dump({"model": model_obj, "scaler": scaler}, MODEL_FILE)

        tier_stats = _compute_tier_stats(completed)

        model = {
            "engine":             _engine_name,
            "factor_names":       FACTOR_NAMES,
            "n_training":         len(completed),
            "r_squared":          round(r2, 4),
            "cv_r2":              round(cv_r2, 4),
            "directional_acc":    round(dir_acc, 1),   # % of directions correct (CV)
            "baseline_acc":       round(baseline, 1),  # baseline = always predict up
            "residual_std":       round(resid_std, 4),
            "mean_return":        round(float(np.mean(y)), 4),
            "last_trained":       datetime.today().strftime("%Y-%m-%d"),
            "feature_importance": importance,
            "tier_stats":         tier_stats,
            "sklearn_model_file": MODEL_FILE,
        }

    except ImportError:
        # Fallback to Ridge if sklearn not available
        X_b     = np.hstack([X, np.ones((len(X), 1))])
        n_feat  = X_b.shape[1]
        reg     = np.eye(n_feat); reg[-1, -1] = 0
        params  = np.linalg.solve(X_b.T @ X_b + RIDGE_LAMBDA * reg, X_b.T @ y_clipped)
        weights = params[:-1].tolist()
        bias    = float(params[-1])
        y_pred  = X_b @ params
        residuals = y_clipped - y_pred
        ss_res  = float(np.sum(residuals ** 2))
        ss_tot  = float(np.sum((y_clipped - np.mean(y_clipped)) ** 2))
        r2      = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        tier_stats = _compute_tier_stats(completed)
        model = {
            "engine":       "ridge_fallback",
            "weights":      weights,
            "bias":         bias,
            "factor_names": FACTOR_NAMES,
            "n_training":   len(completed),
            "r_squared":    round(r2, 4),
            "cv_r2":        None,
            "residual_std": round(float(np.std(residuals)), 4),
            "mean_return":  round(float(np.mean(y)), 4),
            "last_trained": datetime.today().strftime("%Y-%m-%d"),
            "feature_importance": [],
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
        tiers[t].append({
            "ticker": r["ticker"],
            "return": r["actual_return_pct"],
            "score":  round(s, 1),
            "date":   r.get("scored_date", ""),
        })

    stats = {}
    for tier, items in tiers.items():
        if not items:
            continue
        rets = [i["return"] for i in items]
        stats[tier] = {
            "count":      len(items),
            "avg_return": round(sum(rets) / len(rets), 2),
            "win_rate":   round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            "best":       round(max(rets), 2),
            "worst":      round(min(rets), 2),
            "stocks":     sorted(items, key=lambda x: x["return"], reverse=True),
        }
    return stats


# ── Prediction ────────────────────────────────────────────────────────────────

def predict_return(factor_pts: dict) -> "tuple[float, float, int, float] | None":
    """
    Predict 30-day return using the trained model.
    Returns (predicted_return_pct, ±ci_pct_95, n_training, r_squared)
    or None if model not ready yet.

    Uses Gradient Boosting (sklearn) if available, falls back to Ridge.
    """
    data  = _load()
    model = data.get("model")
    if not model or model["n_training"] < MIN_TRAIN:
        return None

    factor_names = model.get("factor_names", FACTOR_NAMES)
    row = np.array([[float(factor_pts.get(name, 0.0) or 0.0) for name in factor_names]])
    ci_95 = model["residual_std"] * 1.96

    # ── sklearn model path (Random Forest or Gradient Boosting) ─────────────
    if model.get("engine") in ("gradient_boosting", "random_forest") and os.path.exists(MODEL_FILE):
        try:
            import joblib
            pkg       = joblib.load(MODEL_FILE)
            gbr       = pkg["model"]
            scaler    = pkg["scaler"]
            X_scaled  = scaler.transform(row)
            predicted = float(gbr.predict(X_scaled)[0])
            # Return directional accuracy % as the 4th element (more meaningful than R²)
            dir_acc = model.get("directional_acc") or round((model.get("cv_r2", 0) + 1) * 50, 1)
            return (
                round(predicted, 2),
                round(ci_95, 2),
                model["n_training"],
                dir_acc,
            )
        except Exception:
            pass  # fall through to Ridge fallback

    # ── Ridge fallback ────────────────────────────────────────────────────────
    weights   = model.get("weights")
    bias      = model.get("bias", 0.0)
    if weights:
        w         = np.array(weights)
        predicted = float(row[0] @ w) + bias
        return (
            round(predicted, 2),
            round(ci_95, 2),
            model["n_training"],
            model.get("r_squared", 0),
        )

    return None


# ── Historical backfill ───────────────────────────────────────────────────────

def backfill_history(tickers: list, months_back: int = 12,
                     progress_cb=None) -> int:
    """
    Generate resolved training records from the past months_back months of
    price history — no waiting required.

    For each ticker and each bi-weekly window going back months_back months:
      - Compute momentum, RSI, 50-day MA, monthly consistency from historical closes
      - Use current fundamentals for financial metrics (revenue, EPS, FCF, etc.)
      - Fetch the actual 30-day return from history
      - Save as a fully resolved record

    progress_cb: optional callable(done, total, ticker) for UI progress updates.
    Returns count of new records added.
    """
    import yfinance as yf
    from fundamentals import fetch_fundamentals
    from scorer import score_stock_detailed

    data  = _load()
    today = datetime.today()
    added = 0

    existing_keys = {
        (r["ticker"], r["scored_date"]) for r in data["records"]
    }

    total_steps = len(tickers) * (months_back * 4)  # approx weekly steps

    step = 0
    for ticker in tickers:
        try:
            t    = yf.Ticker(ticker)
            hist = t.history(period="5y", auto_adjust=True)
            if hist.empty:
                continue

            hist.index = hist.index.tz_localize(None) if hist.index.tzinfo else hist.index
            closes = hist["Close"].dropna()

            # Current fundamentals — used for financial metrics
            fund_base = fetch_fundamentals(ticker)

            # Weekly windows — start as close to today as outcomes allow (need 30d to resolve)
            for weeks_ago in range(5, months_back * 4 + 1, 1):
                step += 1
                if progress_cb:
                    progress_cb(step, total_steps, ticker)

                score_dt   = today - timedelta(weeks=weeks_ago)
                outcome_dt = score_dt + timedelta(days=OUTCOME_DAYS)

                if outcome_dt >= today:
                    continue

                score_str   = score_dt.strftime("%Y-%m-%d")
                outcome_str = outcome_dt.strftime("%Y-%m-%d")

                if (ticker, score_str) in existing_keys:
                    continue

                # Price at score date
                hist_up_to = closes[closes.index <= score_dt]
                if len(hist_up_to) < 60:
                    continue
                price_at_score = float(hist_up_to.iloc[-1])

                # Price at outcome date (first trading day on or after outcome_dt)
                hist_after = closes[
                    (closes.index >= outcome_dt) &
                    (closes.index <= outcome_dt + timedelta(days=7))
                ]
                if hist_after.empty:
                    continue
                price_at_outcome = float(hist_after.iloc[0])

                # Build fund dict with historical price-based metrics
                fund = dict(fund_base)
                fund["current_price"] = price_at_score

                def _mom(days):
                    if len(hist_up_to) > days:
                        p0 = float(hist_up_to.iloc[-days])
                        return round((price_at_score - p0) / p0 * 100, 2) if p0 else None
                    return None

                fund["mom_1m_pct"] = _mom(21)
                fund["mom_3m_pct"] = _mom(63)
                fund["mom_6m_pct"] = _mom(126)

                # RSI-14 at score date
                if len(hist_up_to) >= 15:
                    delta = hist_up_to.diff().dropna()
                    gain  = delta.clip(lower=0).rolling(14).mean()
                    loss  = (-delta.clip(upper=0)).rolling(14).mean()
                    rs    = gain / loss.replace(0, float("nan"))
                    rsi_s = (100 - 100 / (1 + rs)).iloc[-1]
                    fund["rsi"] = round(float(rsi_s), 1) if not np.isnan(rsi_s) else None

                # 50-day MA at score date
                if len(hist_up_to) >= 50:
                    ma50 = float(hist_up_to.rolling(50).mean().iloc[-1])
                    fund["vs_50ma_pct"] = round(
                        (price_at_score - ma50) / ma50 * 100, 2
                    ) if ma50 else None

                # Monthly consistency at score date
                monthly = hist_up_to.resample("ME").last()
                m_rets  = []
                for i in range(1, min(13, len(monthly))):
                    p0 = float(monthly.iloc[-i - 1])
                    p1 = float(monthly.iloc[-i])
                    if p0:
                        m_rets.append((p1 - p0) / p0 * 100)
                if m_rets:
                    fund["positive_months_6"]  = sum(1 for r in m_rets[-6:]  if r > 0)
                    fund["positive_months_12"] = sum(1 for r in m_rets[-12:] if r > 0)

                # Analyst target upside based on historical price
                # (keep current analyst_target — it's the best proxy we have)
                score, _, factor_pts = score_stock_detailed(fund, pol_buys_30d=0)
                actual_return = (price_at_outcome - price_at_score) / price_at_score * 100

                record = {
                    "id":               str(uuid.uuid4())[:8],
                    "scored_date":      score_str,
                    "ticker":           ticker,
                    "price_at_score":   round(price_at_score, 4),
                    "total_score":      round(score, 2),
                    "factor_pts":       factor_pts,
                    "outcome_date":     outcome_str,
                    "price_at_outcome": round(price_at_outcome, 4),
                    "actual_return_pct": round(actual_return, 4),
                    "backfilled":       True,
                }
                data["records"].append(record)
                existing_keys.add((ticker, score_str))
                added += 1

        except Exception:
            continue

    if added:
        _save(data)
        completed = sum(1 for r in data["records"] if r["actual_return_pct"] is not None)
        if completed >= MIN_TRAIN:
            train_model()

    return added


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
        names = model.get("factor_names", [])
        # New Random Forest model — use feature_importance list directly
        if model.get("feature_importance"):
            factor_importance = [
                {"factor": f["factor"], "weight": f["importance"], "abs": f["importance"]}
                for f in model["feature_importance"]
            ]
        # Legacy Ridge model — use weights array
        elif model.get("weights"):
            weights = model["weights"]
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


def get_daily_picks(date_str: str = None) -> dict:
    """
    Return all stocks scored on date_str (defaults to most recent scoring date)
    with ML-predicted 30-day return attached to each.

    Returns:
        {
            "date":   "2026-05-19",
            "stocks": [ {ticker, score, signal, price, predicted_return,
                         ci_95, factor_pts}, ... ]   sorted by predicted_return desc
        }
    """
    data    = _load()
    model   = data.get("model")
    records = data["records"]

    if not records:
        return {"date": None, "stocks": []}

    if date_str is None:
        date_str = max(r["scored_date"] for r in records)

    day_records = [r for r in records if r["scored_date"] == date_str]
    if not day_records:
        return {"date": date_str, "stocks": []}

    from scorer import signal_label

    factor_names = (model["factor_names"] if model else None) or FACTOR_NAMES
    ci_95        = model["residual_std"] * 1.96 if model else None
    n_train      = model["n_training"]          if model else 0

    # Pre-load sklearn model once for batch predictions
    _sklearn_pkg = None
    if model and model.get("engine") in ("random_forest", "gradient_boosting") and os.path.exists(MODEL_FILE):
        try:
            import joblib
            _sklearn_pkg = joblib.load(MODEL_FILE)
        except Exception:
            pass

    # Legacy Ridge weights (fallback)
    _ridge_weights = np.array(model["weights"]) if (model and model.get("weights")) else None
    _ridge_bias    = model["bias"]              if (model and model.get("bias"))    else 0.0

    picks = []
    for r in day_records:
        fp    = r.get("factor_pts") or {}
        label, colour = signal_label(r.get("total_score") or 0)

        predicted = None
        if model and n_train >= MIN_TRAIN:
            row = np.array([[float(fp.get(name, 0.0) or 0.0) for name in factor_names]])
            if _sklearn_pkg:
                try:
                    X_s       = _sklearn_pkg["scaler"].transform(row)
                    predicted = round(float(_sklearn_pkg["model"].predict(X_s)[0]), 2)
                except Exception:
                    pass
            elif _ridge_weights is not None:
                predicted = round(float(row[0] @ _ridge_weights) + _ridge_bias, 2)

        picks.append({
            "ticker":           r["ticker"],
            "score":            r.get("total_score") or 0,
            "signal":           label,
            "colour":           colour,
            "price":            r.get("price_at_score"),
            "predicted_return": predicted,
            "ci_95":            round(ci_95, 2) if ci_95 else None,
            "factor_pts":       fp,
        })

    picks.sort(key=lambda x: (x["predicted_return"] or x["score"]), reverse=True)
    return {"date": date_str, "stocks": picks}
