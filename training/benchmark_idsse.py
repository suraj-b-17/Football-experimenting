"""A detailed by-gap-bucket accuracy table for ONE Bundesliga match, using
whatever model is loaded below (production mean_model.joblib by default).

NOTE what this script does and does NOT prove: pool_train.py's proper
leave-one-match-out evaluation (training/pool_results.csv) is the rigorous
cross-source generalization test in this project — every fold there is
scored on a match the model never trained on. mean_model.joblib is trained
on ALL 30 matches including whichever IDSSE match is picked below, so this
script's numbers are an IN-SAMPLE sanity check (does the shipped model
produce sane, well-calibrated output on real tracking data), not a holdout
test — see BENCHMARKS.md for which number is which.
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import joblib

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import MODEL_DIR
from loader_idsse import load_match, PITCH_X_M, PITCH_Y_M
from kalman_ou import smooth_track

HERE = os.path.dirname(__file__)
THETA_X, SIGMA_X = 0.0247, 2.058
THETA_Y, SIGMA_Y = 0.0311, 1.658
OBS_VAR = 2.25

def build_touch_waypoints(events, player_id, period_to_offset):
    ev = events[events["player_id"] == player_id]
    pts = []
    for _, row in ev.iterrows():
        if pd.isna(row["Start X_m"]):
            continue
        t = row["Start Time [s]"] + period_to_offset.get(row["Period"], 0)
        pts.append((t, row["Start X_m"], row["Start Y_m"]))
    pts.sort(key=lambda p: p[0])
    return pts

def run(match_id="J03WPY"):
    game = load_match(match_id)
    players, ball, events = game["players"], game["ball"], game["events"]
    model = joblib.load(os.path.join(MODEL_DIR, "mean_model.joblib"))

    period_ends = players.groupby("period")["t"].max()
    period_to_offset = {1: 0.0}
    if 2 in period_ends.index:
        period_to_offset[2] = period_ends.loc[1]

    events = events.dropna(subset=["Start X_m"]).copy()
    events["t"] = events["Start Time [s]"] + events["Period"].map(period_to_offset)
    events = events.sort_values("t")
    # Start X_m/Y_m are each event's own team's "own goal at 0" frame
    # (loader_idsse.py) -- home's and away's frames are permanent mirror
    # images. Normalize to a common (home) frame here; flip back per-team
    # at use, same fix as benchmark.py / apply_to_match.py.
    is_home_ev = events["Team"] == "home"
    bt = events["t"].values
    bx = np.where(is_home_ev, events["Start X_m"].values, PITCH_X_M - events["Start X_m"].values)
    by = np.where(is_home_ev, events["Start Y_m"].values, PITCH_Y_M - events["Start Y_m"].values)
    poss = events[["t", "Team"]]

    results = []
    for pid in players["player_id"].unique():
        team = players[players["player_id"] == pid]["team"].iloc[0]
        waypoints = build_touch_waypoints(events, pid, period_to_offset)
        if len(waypoints) < 4:
            continue
        wt = np.array([w[0] for w in waypoints])
        wx = np.array([w[1] for w in waypoints])
        wy = np.array([w[2] for w in waypoints])
        anchor_x, anchor_y = np.median(wx), np.median(wy)

        t_min, t_max = wt.min(), wt.max()
        grid = np.arange(t_min, t_max, 5.0)
        if len(grid) < 3:
            continue

        gbx = np.interp(grid, bt, bx)
        gby = np.interp(grid, bt, by)
        if team != "home":
            gbx = PITCH_X_M - gbx
            gby = PITCH_Y_M - gby
        idx = poss["t"].searchsorted(grid, side="right") - 1
        idx = np.clip(idx, 0, len(poss) - 1)
        poss_team = poss["Team"].values[idx]
        poss_flag = (poss_team == team).astype(float)

        X = np.column_stack([
            np.full(len(grid), anchor_x), np.full(len(grid), anchor_y),
            gbx - anchor_x, gby - anchor_y, poss_flag,
        ])
        pred = model.predict(X)
        mu_x = anchor_x + pred[:, 0]
        mu_y = anchor_y + pred[:, 1]

        kx, ky, px, py = smooth_track(grid, mu_x, mu_y, wt, wx, wy,
                                       THETA_X, SIGMA_X, THETA_Y, SIGMA_Y, OBS_VAR)

        truth = players[players["player_id"] == pid].dropna(subset=["x_m", "y_m"])
        truth = truth[(truth["t"] + truth["period"].map(period_to_offset) >= t_min) &
                      (truth["t"] + truth["period"].map(period_to_offset) <= t_max)]
        if truth.empty:
            continue
        truth_t = (truth["t"] + truth["period"].map(period_to_offset)).values
        keep = np.arange(0, len(truth_t), 25)
        truth_t = truth_t[keep]
        truth_x = truth["x_m"].values[keep]
        truth_y = truth["y_m"].values[keep]

        kx_i = np.interp(truth_t, grid, kx)
        ky_i = np.interp(truth_t, grid, ky)
        mu_x_i = np.interp(truth_t, grid, mu_x)
        mu_y_i = np.interp(truth_t, grid, mu_y)
        sd_x_i = np.sqrt(np.interp(truth_t, grid, np.maximum(px, 0)))
        sd_y_i = np.sqrt(np.interp(truth_t, grid, np.maximum(py, 0)))
        sd_i = np.hypot(sd_x_i, sd_y_i)
        gap = np.array([np.min(np.abs(wt - t)) for t in truth_t])
        err_k = np.hypot(kx_i - truth_x, ky_i - truth_y)
        err_m = np.hypot(mu_x_i - truth_x, mu_y_i - truth_y)
        for g, ek, em, sd in zip(gap, err_k, err_m, sd_i):
            results.append((g, ek, em, sd))

    return pd.DataFrame(results, columns=["gap_s", "err_kalman", "err_mu_only", "sd"])

if __name__ == "__main__":
    df = run("J03WPY")
    print("Bundesliga (IDSSE) detail view — production model (in-sample; see docstring)")
    print("total scored samples:", len(df))
    bins = [0, 5, 10, 20, 30, 60, 120, 99999]
    labels = ["0-5s", "5-10s", "10-20s", "20-30s", "30-60s", "60-120s", "120s+"]
    df["bucket"] = pd.cut(df["gap_s"], bins=bins, labels=labels)
    summary = df.groupby("bucket").agg(
        n=("err_kalman", "size"),
        kalman_median=("err_kalman", "median"),
        kalman_p90=("err_kalman", lambda s: s.quantile(0.9)),
        mu_only_median=("err_mu_only", "median"),
    )
    pd.set_option("display.width", 140)
    print(summary)
    print("\noverall median error: kalman=%.2fm mu_only=%.2fm" % (
        df["err_kalman"].median(), df["err_mu_only"].median()))

    # Genuine OUT-OF-SAMPLE calibration check: k50/k68/k90 were derived from
    # the Metrica Game 2 benchmark, never from this Bundesliga data at all.
    cov50 = (df["err_kalman"] < df["sd"] * 1.140).mean()
    cov90 = (df["err_kalman"] < df["sd"] * 2.716).mean()
    print(f"calibration transfer check (multipliers fit on Metrica, applied here blind):")
    print(f"  50%-band actual coverage on Bundesliga data: {cov50:.1%}")
    print(f"  90%-band actual coverage on Bundesliga data: {cov90:.1%}")

    df.to_csv(os.path.join(HERE, "benchmark_idsse_results.csv"), index=False)
