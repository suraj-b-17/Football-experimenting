"""Reproduces the OU residual parameters (theta, sigma per axis) used by
kalman_ou.py, fit from Metrica Sample_Game_1 only (Game_2 stays held out for
benchmark.py). Also reports the empirical uncertainty-calibration multipliers
(the model's raw Kalman variance is under-confident; these multipliers,
computed once on the Game_2 benchmark, correct it to match true coverage).
"""
import os
import sys
import warnings
import numpy as np
import joblib

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import MODEL_DIR
from loader_metrica import load_game
from fit_mean_model import build_training_rows

HERE = os.path.dirname(__file__)

def lag1_autocorr(r):
    return np.corrcoef(r[:-1], r[1:])[0, 1]

if __name__ == "__main__":
    # Same game-2-excluded model benchmark.py uses (see its comment) — this
    # script's OU residuals feed the same Kalman smoother benchmark.py scores.
    model = joblib.load(os.path.join(MODEL_DIR, "mean_model_no_metrica_game2.joblib"))
    game1 = load_game("Sample_Game_1")
    df = build_training_rows(game1, sample_every_s=1.0).dropna().reset_index(drop=True)
    X = df[["anchor_x", "anchor_y", "ball_rel_x", "ball_rel_y", "possession"]].values
    pred = model.predict(X)
    resid_x = df["offset_x"].values - pred[:, 0]
    resid_y = df["offset_y"].values - pred[:, 1]

    ac_x, ac_y = lag1_autocorr(resid_x), lag1_autocorr(resid_y)
    dt = 1.0  # sample_every_s used above
    theta_x, theta_y = -np.log(ac_x) / dt, -np.log(ac_y) / dt
    sd_x, sd_y = resid_x.std(), resid_y.std()
    sigma_x = sd_x * np.sqrt(2 * theta_x)
    sigma_y = sd_y * np.sqrt(2 * theta_y)

    print(f"THETA_X, SIGMA_X = {theta_x:.4f}, {sigma_x:.3f}")
    print(f"THETA_Y, SIGMA_Y = {theta_y:.4f}, {sigma_y:.3f}")
    print(f"(mean-reversion half-life: x={np.log(2)/theta_x:.1f}s, y={np.log(2)/theta_y:.1f}s)")
    print("These are hardcoded into kalman_ou-consuming scripts (benchmark.py, apply_to_match.py) —")
    print("re-run this after any change to fit_mean_model.py's features/training and update them there.")
