"""
FinBERT sentiment analyser — free, runs fully locally on your Mac.
Model: ProsusAI/finbert (~400 MB, downloaded once from HuggingFace)

Why FinBERT over keyword matching:
  - Reads the FULL sentence, not individual words
  - Understands negation:  "not weak"  → positive  ✓
  - Understands context:   "Despite strong earnings, concerns remain"  → negative  ✓
  - Trained specifically on financial news text
  - Returns a confidence score (how sure the model is)

Usage:
    from finbert_sentiment import score_headlines, is_available

    if is_available():
        result = score_headlines(["Apple beats earnings", "Shares fall on weak guidance"])
        print(result["sentiment_score"])   # net positive score
"""

import os
import threading

# ── Lazy model loading ─────────────────────────────────────────────────────────
# Model is loaded once on first call and kept in memory.
# ~400 MB download on first use, cached in ~/.cache/huggingface/

_MODEL_NAME = "ProsusAI/finbert"
_pipeline   = None
_load_lock  = threading.Lock()
_load_error = None          # stores error message if loading failed


def _load():
    """Load FinBERT pipeline once. Thread-safe."""
    global _pipeline, _load_error
    if _pipeline is not None or _load_error is not None:
        return
    with _load_lock:
        if _pipeline is not None or _load_error is not None:
            return   # another thread loaded it while we waited
        try:
            from transformers import pipeline as _hf_pipeline
            import warnings
            # Suppress the HuggingFace progress bars and warnings in background threads
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _pipeline = _hf_pipeline(
                    "text-classification",
                    model=_MODEL_NAME,
                    tokenizer=_MODEL_NAME,
                    device=-1,          # -1 = CPU (Intel Mac has no CUDA GPU)
                    truncation=True,
                    max_length=512,
                )
        except Exception as e:
            _load_error = str(e)


def is_available() -> bool:
    """Return True if FinBERT loaded successfully."""
    _load()
    return _pipeline is not None


def load_status() -> dict:
    """Return current load status — useful for UI display."""
    if _pipeline is not None:
        return {"status": "ready", "message": "FinBERT loaded ✓"}
    if _load_error:
        return {"status": "error", "message": _load_error}
    return {"status": "not_loaded", "message": "Not loaded yet"}


# ── Core scoring ───────────────────────────────────────────────────────────────

def score_headlines(headlines: list, batch_size: int = 16) -> dict:
    """
    Score a list of news headlines using FinBERT.

    Each headline is classified as positive / negative / neutral with a
    confidence score (0–1).  The aggregate result uses confidence-weighted
    counting so a headline with 95% confidence counts more than one at 55%.

    Parameters
    ----------
    headlines : list of str
        Up to 50 headlines (extras are ignored — diminishing returns beyond that).
    batch_size : int
        How many headlines to send to the model at once.  16 is a safe default
        for 16 GB Intel Mac.

    Returns
    -------
    {
        "sentiment_score":  float,   # confidence-weighted net score (pos - neg)
        "positive_count":   int,     # headlines classified positive
        "negative_count":   int,     # headlines classified negative
        "neutral_count":    int,
        "total_scored":     int,
        "avg_confidence":   float,   # mean confidence across all headlines
        "method":           str,     # "finbert"
        "error":            None | str,
    }
    """
    if not headlines:
        return _empty("no headlines provided")

    _load()
    if _pipeline is None:
        return _empty(_load_error or "FinBERT not loaded")

    try:
        texts = [h.strip() for h in headlines[:50] if h.strip()]
        if not texts:
            return _empty("no valid headlines")

        # Run FinBERT in batches
        results = _pipeline(texts, batch_size=batch_size)

        pos_score = neg_score = 0.0
        pos_count = neg_count = neu_count = 0
        confidences = []

        for res in results:
            label      = res["label"].lower()   # "positive" / "negative" / "neutral"
            confidence = float(res["score"])    # 0–1
            confidences.append(confidence)

            if label == "positive":
                pos_score += confidence
                pos_count += 1
            elif label == "negative":
                neg_score += confidence
                neg_count += 1
            else:
                neu_count += 1

        net = round(pos_score - neg_score, 3)   # confidence-weighted net score

        return {
            "sentiment_score": net,
            "positive_count":  pos_count,
            "negative_count":  neg_count,
            "neutral_count":   neu_count,
            "total_scored":    len(texts),
            "avg_confidence":  round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
            "method":          "finbert",
            "error":           None,
        }

    except Exception as e:
        return _empty(str(e))


def _empty(error: str) -> dict:
    return {
        "sentiment_score": None,
        "positive_count":  0,
        "negative_count":  0,
        "neutral_count":   0,
        "total_scored":    0,
        "avg_confidence":  0.0,
        "method":          "finbert",
        "error":           error,
    }


# ── Background preload ─────────────────────────────────────────────────────────

def preload_async():
    """
    Start loading the model in a background thread so it's ready
    before the first stock is scored.  Call once at app startup.
    """
    t = threading.Thread(target=_load, daemon=True)
    t.start()
