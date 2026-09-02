from typing import Tuple, Dict, Any, List
import pandas as pd
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss

class ProbabilityCalibrator:
    def __init__(self):
        self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self.is_fitted = False

    def fit(self, y_prob_uncalibrated: pd.Series, y_true: pd.Series):
        """Fits the calibrator on a CALIBRATION_SET."""
        self.calibrator.fit(y_prob_uncalibrated, y_true)
        self.is_fitted = True
        return self

    def transform(self, y_prob_uncalibrated: pd.Series) -> pd.Series:
        """Transforms probabilities using the fitted calibrator."""
        if not self.is_fitted:
            raise RuntimeError("Calibrator is not fitted.")
        calibrated = self.calibrator.predict(y_prob_uncalibrated)
        return pd.Series(calibrated, index=y_prob_uncalibrated.index)

def calculate_brier_score(y_true: pd.Series, y_prob: pd.Series) -> float:
    return float(brier_score_loss(y_true, y_prob))

def calculate_calibration_curve(y_true: pd.Series, y_prob: pd.Series, n_bins: int = 10) -> Dict[str, List[float]]:
    from sklearn.calibration import calibration_curve
    prob_true, prob_pred = calibration_curve(y_true, y_prob, n_bins=n_bins)
    return {
        "prob_true": [float(x) for x in prob_true],
        "prob_pred": [float(x) for x in prob_pred]
    }
