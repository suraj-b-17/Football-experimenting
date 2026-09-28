"""Benchmarks reconstruction accuracy on Metrica Sample_Game_2 (held out from
model fitting), by throwing away everything except what a touch-event feed
would give us (each player's position only at their own touches), then
scoring the reconstruction against the real tracking data we deliberately
hid. Reports error as a function of time since the nearest touch, and
compares the new ball-conditioned Kalman/OU model against the plain
spline used previously (fed the exact same masked input).
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
from loader_metrica import load_game, PITCH_X_M, PITCH_Y_M
from kalman_ou import smooth_track

HERE = os.path.dirname(__file__)

# OU parameters fit on Game 1 residuals (see fit_ou_params.py)
THETA_X, SIGMA_X = 0.0247, 2.058
THETA_Y, SIGMA_Y = 0.0311, 1.658
OBS_VAR = 2.25  # ~1.5m touch-tagging noise, per axis

def build_touch_waypoints(events, player_col_val, period_to_offset):
    """Each event with From==player gives a real (t, x, y) touch, t made
    continuous across periods the same way match_animator.py does (period 2
    continues from where period 1 ended)."""
    ev = events[events["From"] == player_col_val]
    pts = []
    for _, row in ev.iterrows():
        if pd.isna(row["Start X_m"]):
            continue
        t = row["Start Time [s]"] + period_to_offset.get(row["Period"], 0)
        pts.append((t, row["Start X_m"], row["Start Y_m"]))
    pts.sort(key=lambda p: p[0])
    return pts

def masked_ball_trajectory(events, period_to_offset):
    """Start X_m/Y_m are already each event's OWN team's "own goal at 0"
    frame (loader.py) -- home's and away's frames are permanent mirror
    images of each other. Pooling both teams' events into one trajectory
    without correcting for that makes the ball appear to teleport every
    time the touching team changes. Normalize to a single common frame
    (Home's) here; callers flip back to whichever team's own frame they
    need, same fix as apply_to_match.py's build_ball_and_possession."""
    pts = []
    for _, row in events.sort_values("Start Time [s]").iterrows():
        if pd.isna(row["Start X_m"]):
            continue
        t = row["Start Time [s]"] + period_to_offset.get(row["Period"], 0)
        is_home = row["Team"] == "Home"
        cx = row["Start X_m"] if is_home else PITCH_X_M - row["Start X_m"]
        cy = row["Start Y_m"] if is_home else PITCH_Y_M - row["Start Y_m"]
        pts.append((t, cx, cy))
    pts.sort(key=lambda p: p[0])
    return pts

def possession_series(events, period_to_offset):
    ev = events[["Start Time [s]", "Period", "Team"]].dropna().copy()
    ev["t"] = ev["Start Time [s]"] + ev["Period"].map(period_to_offset)
    return ev.sort_values("t")

def role_bucket(anchor_x):
    """Coarse role from median touch depth — Metrica has no position labels,
    so this is an approximation, only used to break the error table down by
    role rather than to feed the model."""
    if anchor_x < 18:
        return "GK"
    if anchor_x < 35:
        return "DEF"
    if anchor_x < 55:
        return "MID"
    return "FWD"

def spline_baseline(grid_t, waypoints):
    """The plain monotone-ish reconstruction used before this session's
    Kalman upgrade: linear interpolation between a player's own touches
    (a fair stand-in for the shipped smoothstep/PCHIP spline — both just
    interpolate between the same waypoints with no ball-conditioning)."""
    if len(waypoints) == 0:
        return np.zeros_like(grid_t), np.zeros_like(grid_t)
    wt = np.array([w[0] for w in waypoints])
    wx = np.array([w[1] for w in waypoints])
    wy = np.array([w[2] for w in waypoints])
    x = np.interp(grid_t, wt, wx)
    y = np.interp(grid_t, wt, wy)
    return x, y

def run_benchmark(game_name="Sample_Game_2"):
    game = load_game(game_name)
    players, ball, events = game["players"], game["ball"], game["events"]
    # MUST be the game-2-excluded model — this benchmark scores game 2, so
    # the production mean_model.joblib (trained on ALL matches, including
    # this one) would silently become an in-sample check otherwise. This
    # exact mistake was made and caught once already in this project's
    # history (see the old README's write-up); the fix here is to never
    # let this script load the production model file at all.
    model = joblib.load(os.path.join(MODEL_DIR, "mean_model_no_metrica_game2.joblib"))

    # continuous match-clock offset per period (period 2 continues after period 1)
    period_ends = players.groupby("period")["t"].max()
    period_to_offset = {1: 0.0}
    if 2 in period_ends.index:
        period_to_offset[2] = period_ends.loc[1]

    events = events.copy()
    events["Period"] = events["Period"].astype(int)

    ball_pts = masked_ball_trajectory(events, period_to_offset)
    bt = np.array([p[0] for p in ball_pts])
    bx = np.array([p[1] for p in ball_pts])
    by = np.array([p[2] for p in ball_pts])

    poss = possession_series(events, period_to_offset)

    results = []  # (gap_since_touch, err_kalman, err_spline, err_mu_only)

    player_ids = players["player_id"].unique()
    for pid in player_ids:
        team = players[players["player_id"] == pid]["team"].iloc[0]
        num = pid.split("_")[1]
        waypoints = build_touch_waypoints(events, f"Player{num}", period_to_offset)
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
        if team != "Home":
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
        offset_pred = model.predict(X)
        mu_x = anchor_x + offset_pred[:, 0]
        mu_y = anchor_y + offset_pred[:, 1]

        kx, ky, px, py = smooth_track(grid, mu_x, mu_y, wt, wx, wy,
                                       THETA_X, SIGMA_X, THETA_Y, SIGMA_Y, OBS_VAR)
        sx, sy = spline_baseline(grid, waypoints)
        role = role_bucket(anchor_x)

        # score against REAL tracking at every real tracked frame in-range
        truth = players[(players["player_id"] == pid)].copy()
        truth = truth.dropna(subset=["x_m", "y_m"])
        truth = truth[truth.apply(lambda r: r["t"] + period_to_offset.get(r["period"], 0) >= t_min and
                                             r["t"] + period_to_offset.get(r["period"], 0) <= t_max, axis=1)]
        if truth.empty:
            continue
        truth_t = (truth["t"] + truth["period"].map(period_to_offset)).values
        # subsample ground truth to 1 per second (25fps is overkill and slow)
        keep = np.arange(0, len(truth_t), 25)
        truth_t = truth_t[keep]
        truth_x = truth["x_m"].values[keep]
        truth_y = truth["y_m"].values[keep]

        kalman_x_i = np.interp(truth_t, grid, kx)
        kalman_y_i = np.interp(truth_t, grid, ky)
        spline_x_i = np.interp(truth_t, grid, sx)
        spline_y_i = np.interp(truth_t, grid, sy)
        mu_x_i = np.interp(truth_t, grid, mu_x)
        mu_y_i = np.interp(truth_t, grid, mu_y)
        sd_x_i = np.sqrt(np.interp(truth_t, grid, np.maximum(px, 0)))
        sd_y_i = np.sqrt(np.interp(truth_t, grid, np.maximum(py, 0)))
        sd_i = np.hypot(sd_x_i, sd_y_i)

        gap = np.array([np.min(np.abs(wt - t)) for t in truth_t])
        err_k = np.hypot(kalman_x_i - truth_x, kalman_y_i - truth_y)
        err_s = np.hypot(spline_x_i - truth_x, spline_y_i - truth_y)
        err_m = np.hypot(mu_x_i - truth_x, mu_y_i - truth_y)
        # "frozen at formation slot": never moves from their own median touch
        # position at all — the naive baseline before ANY reconstruction.
        err_f = np.hypot(anchor_x - truth_x, anchor_y - truth_y)

        for g, ek, es, em, ef, sd in zip(gap, err_k, err_s, err_m, err_f, sd_i):
            results.append((g, ek, es, em, ef, sd, role))

    df = pd.DataFrame(results, columns=["gap_s", "err_kalman", "err_spline", "err_mu_only", "err_frozen", "sd", "role"])
    return df

if __name__ == "__main__":
    df = run_benchmark("Sample_Game_2")
    print("total scored samples:", len(df))
    bins = [0, 5, 10, 20, 30, 60, 120, 99999]
    labels = ["0-5s", "5-10s", "10-20s", "20-30s", "30-60s", "60-120s", "120s+"]
    df["bucket"] = pd.cut(df["gap_s"], bins=bins, labels=labels)

    print("\n=== by time-since-touch (all roles pooled) ===")
    summary = df.groupby("bucket").agg(
        n=("err_kalman", "size"),
        kalman_median=("err_kalman", "median"),
        kalman_p90=("err_kalman", lambda s: s.quantile(0.9)),
        spline_median=("err_spline", "median"),
        frozen_median=("err_frozen", "median"),
        mu_only_median=("err_mu_only", "median"),
    )
    pd.set_option("display.width", 160)
    print(summary)

    print("\n=== by role x time-since-touch (Kalman/OU median error, meters) ===")
    role_table = df.groupby(["role", "bucket"])["err_kalman"].median().unstack("bucket")
    print(role_table)

    print("\n=== by role x time-since-touch (frozen-at-formation-slot median error, meters) ===")
    role_table_frozen = df.groupby(["role", "bucket"])["err_frozen"].median().unstack("bucket")
    print(role_table_frozen)
    role_table_frozen.to_csv(os.path.join(HERE, "benchmark_by_role_frozen.csv"))

    print("\n=== where does frozen beat kalman? (kalman_median - frozen_median, negative = kalman wins) ===")
    print(role_table - role_table_frozen)

    print("\n=== calibration: does the reported spread actually predict error? ===")
    ratio = df["err_kalman"] / df["sd"].replace(0, np.nan)
    ratio = ratio.dropna()
    k50, k68, k90 = ratio.quantile(0.5), ratio.quantile(0.68), ratio.quantile(0.9)
    print(f"empirical radius multipliers needed for true coverage: k50={k50:.3f} k68={k68:.3f} k90={k90:.3f}")
    cov_nominal_90 = (df["err_kalman"] < df["sd"] * 2.716).mean()  # multiplier currently shipped
    cov_nominal_50 = (df["err_kalman"] < df["sd"] * 1.140).mean()
    print(f"with the SHIPPED multipliers (k50=1.140, k90=2.716): 50%-band actual coverage={cov_nominal_50:.1%}, 90%-band actual coverage={cov_nominal_90:.1%}")

    df.to_csv(os.path.join(HERE, "benchmark_results.csv"), index=False)
    summary.to_csv(os.path.join(HERE, "benchmark_summary.csv"))
    role_table.to_csv(os.path.join(HERE, "benchmark_by_role.csv"))
    print("\noverall median error: kalman=%.2fm spline=%.2fm frozen=%.2fm mu_only=%.2fm" % (
        df["err_kalman"].median(), df["err_spline"].median(), df["err_frozen"].median(), df["err_mu_only"].median()))
