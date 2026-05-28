"""
Headless daily job — no Streamlit required.
Run manually:  python3 daily_job.py
Or via scheduled task (Claude Code / cron).

What it does:
  1. Resolve any overdue 30-day outcomes (uses historical prices — safe to run late)
  2. Retrain the prediction model if new outcomes exist
  3. Score the configured universe and save new data points to learning history
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

import pandas as pd

from fundamentals import fetch_fundamentals
from scorer import score_stock_detailed
from score_history import update_outcomes, save_scores, train_model, get_stats
from scraper import load_cache
from job_state import JobState

# ── Configuration ─────────────────────────────────────────────────────────────
# Which stocks to score daily for the learning history.
# Options: "gov_purchased" | "gov_all" | list of tickers e.g. ["AAPL","MSFT"]
DAILY_UNIVERSE = "gov_purchased"
MIN_PRICE      = 5.0   # skip penny stocks


def get_universe() -> list[str]:
    if isinstance(DAILY_UNIVERSE, list):
        return DAILY_UNIVERSE

    trades = load_cache()
    if not trades:
        return []
    df = pd.DataFrame(trades)
    df["ticker"]           = df["ticker"].fillna("").str.upper().str.strip()
    df["transaction_type"] = df["transaction_type"].fillna("")

    if DAILY_UNIVERSE == "gov_purchased":
        mask = df["transaction_type"].str.contains("Purchase", case=False, na=False)
        tickers = df.loc[mask, "ticker"].unique()
    else:
        tickers = df["ticker"].unique()

    return sorted(t for t in tickers if t not in ("N/A", "--", ""))


def run():
    print(f"\n{'='*55}")
    print(f"  Daily Job — {datetime.today().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    # Step 1: resolve overdue outcomes
    print("\n[1/3] Resolving 30-day outcomes…")
    resolved = update_outcomes()
    print(f"      {resolved} outcome(s) resolved.")

    # Step 2: score today's universe
    print(f"\n[2/3] Scoring universe: {DAILY_UNIVERSE}…")
    tickers = get_universe()
    if not tickers:
        print("      No tickers found — check scraper cache.")
    else:
        print(f"      {len(tickers)} tickers to score.")
        # Update sidebar job state so the UI shows real progress
        _js = JobState("growth_report")
        _js.start(total=len(tickers), meta={"universe_key": DAILY_UNIVERSE})
        batch  = []
        errors = 0
        for i, ticker in enumerate(tickers, 1):
            try:
                fund = fetch_fundamentals(ticker)
                if fund.get("error") and not fund.get("current_price"):
                    errors += 1
                    continue
                price = fund.get("current_price") or 0
                if price < MIN_PRICE:
                    continue
                score, _, factor_pts = score_stock_detailed(fund, pol_buys_30d=0)
                batch.append({"ticker": ticker, "price": price, "score": score, "factor_pts": factor_pts})
                if i % 20 == 0:
                    print(f"      {i}/{len(tickers)} done…")
                    _js.update(done=i, current=ticker)
            except Exception as e:
                errors += 1
        added = save_scores(batch)
        _js.finish(result=batch)
        print(f"      {added} new score(s) saved. {errors} error(s) skipped.")

    # Step 3: show model status
    print("\n[3/3] Model status…")
    stats = get_stats()
    print(f"      Total tracked: {stats['n_total']}")
    print(f"      Outcomes resolved: {stats['n_completed']}")
    print(f"      Pending (< 30 days): {stats['n_pending']}")
    model = stats.get("model")
    if model:
        print(f"      Model: active — {model['n_training']} training points, R²={model['r_squared']:.3f}")
    else:
        needed = stats["needs_more"]
        print(f"      Model: learning — need {needed} more resolved outcomes to activate")

    print(f"\n  Done. {datetime.today().strftime('%H:%M:%S')}\n")


if __name__ == "__main__":
    run()
