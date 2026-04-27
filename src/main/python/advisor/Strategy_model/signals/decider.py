import numpy as np


class SignalDecision:

    def __init__(self, threshold=0.3):
        self.threshold = threshold

    def generate(self, df):
        df = df.copy()

        df["Signal"] = np.where(
            (df["Filtered"]) & (df["Score"] > self.threshold),
            "Buy",
            np.where(
                (df["Filtered"]) & (df["Score"] < -self.threshold),
                "Sell",
                None
            )
        )

        return df

    def build_signal(
        self,
        direction: str,
        confidence: float,
        metadata: dict | None = None,
        frame=None,
    ) -> dict | None:
        direction_lc = str(direction or "").strip().lower()
        if direction_lc not in {"buy", "sell"}:
            return None

        payload = {
            "sig": "bullish" if direction_lc == "buy" else "bearish",
            "direction": direction_lc.title(),
            "confidence": float(confidence),
            "metadata": dict(metadata or {}),
        }
        if frame is not None:
            payload["frame"] = frame
        score = payload["metadata"].get("score")
        if score is not None:
            payload["score"] = score
        return payload
