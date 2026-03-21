"""
ML Pattern Recognition — trains models on engineered features to predict
price direction. Uses walk-forward training to avoid look-ahead bias.

Models: Random Forest, XGBoost, LightGBM, Ensemble.
"""

import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report
from sklearn.preprocessing import StandardScaler

try:
    from xgboost import XGBClassifier
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    from lightgbm import LGBMClassifier
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

import config
from features import compute_features, get_feature_columns

logger = logging.getLogger(__name__)


class MLPatternDetector:
    """
    Walk-forward ML pattern detector.
    Trains on historical data, predicts on recent data.
    """

    def __init__(
        self,
        target_col: str = "target_up_1d",
        n_estimators: int = config.ML_N_ESTIMATORS,
        min_confidence: float = config.ML_MIN_CONFIDENCE,
    ):
        self.target_col = target_col
        self.n_estimators = n_estimators
        self.min_confidence = min_confidence
        self.model = None
        self.scaler = StandardScaler()
        self.feature_cols: list[str] = []
        self.feature_importances: Optional[pd.Series] = None

    def _build_ensemble(self):
        """Build a voting ensemble of available models."""
        estimators = [
            ("rf", RandomForestClassifier(
                n_estimators=self.n_estimators,
                max_depth=8,
                min_samples_leaf=20,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1,
            )),
        ]

        if HAS_XGB:
            estimators.append(("xgb", XGBClassifier(
                n_estimators=self.n_estimators,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
                verbosity=0,
            )))

        if HAS_LGBM:
            estimators.append(("lgbm", LGBMClassifier(
                n_estimators=self.n_estimators,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1,
                verbose=-1,
            )))

        if len(estimators) > 1:
            return VotingClassifier(estimators=estimators, voting="soft")
        else:
            return estimators[0][1]

    def train(self, df: pd.DataFrame) -> dict:
        """
        Train the ML model using walk-forward cross-validation.

        Returns dict with training metrics.
        """
        logger.info("Computing features for ML training...")
        feat = compute_features(df)
        self.feature_cols = get_feature_columns(feat)

        # Prepare data
        combined = feat[self.feature_cols + [self.target_col]].dropna()

        if len(combined) < 100:
            logger.warning("Insufficient data for ML training")
            return {"error": "insufficient_data"}

        X = combined[self.feature_cols]
        y = combined[self.target_col]

        # Scale features
        X_scaled = pd.DataFrame(
            self.scaler.fit_transform(X),
            index=X.index,
            columns=X.columns,
        )

        # Walk-forward cross-validation
        tscv = TimeSeriesSplit(n_splits=5)
        cv_scores = []

        for fold, (train_idx, test_idx) in enumerate(tscv.split(X_scaled)):
            X_train = X_scaled.iloc[train_idx]
            y_train = y.iloc[train_idx]
            X_test = X_scaled.iloc[test_idx]
            y_test = y.iloc[test_idx]

            model = self._build_ensemble()
            model.fit(X_train, y_train)
            preds = model.predict(X_test)
            score = accuracy_score(y_test, preds)
            cv_scores.append(score)
            logger.info(f"  Fold {fold+1}: accuracy={score:.3f}")

        # Train final model on all data
        self.model = self._build_ensemble()
        self.model.fit(X_scaled, y)

        # Feature importances (from RF component)
        try:
            if hasattr(self.model, "estimators_"):
                # VotingClassifier
                rf = self.model.named_estimators_.get("rf")
                if rf:
                    self.feature_importances = pd.Series(
                        rf.feature_importances_, index=self.feature_cols
                    ).sort_values(ascending=False)
            elif hasattr(self.model, "feature_importances_"):
                self.feature_importances = pd.Series(
                    self.model.feature_importances_, index=self.feature_cols
                ).sort_values(ascending=False)
        except Exception:
            pass

        metrics = {
            "cv_scores": cv_scores,
            "mean_cv_accuracy": np.mean(cv_scores),
            "std_cv_accuracy": np.std(cv_scores),
            "n_features": len(self.feature_cols),
            "n_samples": len(combined),
            "top_features": (
                self.feature_importances.head(10).to_dict()
                if self.feature_importances is not None else {}
            ),
        }

        logger.info(f"ML training complete: mean_accuracy={metrics['mean_cv_accuracy']:.3f} "
                     f"± {metrics['std_cv_accuracy']:.3f}")

        return metrics

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate signals from the trained model.

        Returns DataFrame with 'signal' column and 'ml_confidence' column.
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")

        feat = compute_features(df)
        X = feat[self.feature_cols].dropna()

        if X.empty:
            out = df.copy()
            out["signal"] = 0
            out["ml_confidence"] = 0.0
            return out

        X_scaled = pd.DataFrame(
            self.scaler.transform(X), index=X.index, columns=X.columns
        )

        # Get probability predictions
        probas = self.model.predict_proba(X_scaled)
        # probas[:, 1] = probability of class 1 (price goes up)
        prob_up = probas[:, 1] if probas.shape[1] > 1 else probas[:, 0]

        pred_series = pd.Series(prob_up, index=X.index)

        out = df.copy()
        out["ml_confidence"] = 0.0
        out.loc[pred_series.index, "ml_confidence"] = pred_series

        out["signal"] = 0
        out.loc[pred_series[pred_series > self.min_confidence].index, "signal"] = 1
        out.loc[pred_series[pred_series < (1 - self.min_confidence)].index, "signal"] = -1

        return out

    def walk_forward_predict(
        self,
        df: pd.DataFrame,
        train_pct: float = config.ML_TRAIN_TEST_SPLIT,
    ) -> pd.DataFrame:
        """
        Walk-forward prediction: train on first train_pct%, predict on rest.
        More realistic than training on all data then predicting.
        """
        split_idx = int(len(df) * train_pct)
        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]

        if len(train_df) < 100 or len(test_df) < 20:
            out = df.copy()
            out["signal"] = 0
            out["ml_confidence"] = 0.0
            return out

        self.train(train_df)
        return self.predict(df)  # Returns signals for full range, but model only saw train

    def save(self, path: str = "ml_model.pkl"):
        """Save trained model to disk."""
        with open(path, "wb") as f:
            pickle.dump({
                "model": self.model,
                "scaler": self.scaler,
                "feature_cols": self.feature_cols,
                "target_col": self.target_col,
                "min_confidence": self.min_confidence,
            }, f)
        logger.info(f"Model saved to {path}")

    def load(self, path: str = "ml_model.pkl"):
        """Load trained model from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.scaler = data["scaler"]
        self.feature_cols = data["feature_cols"]
        self.target_col = data["target_col"]
        self.min_confidence = data["min_confidence"]
        logger.info(f"Model loaded from {path}")


def ml_pattern_signal(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience function matching the pattern interface.
    Trains an ML model walk-forward and returns signals.
    """
    detector = MLPatternDetector()
    return detector.walk_forward_predict(df)
