"""Leave-one-match-out evaluation and training of the sparsity-matched model.

Held-out folds: the 10 fully tracked matches (Metrica x3, DFL/IDSSE x7). Every
method is scored on the SAME simulated StatsBomb-like inputs (sparsity_sim) for
the held-out match:
  OLD        the previously shipped pipeline, reproduced exactly: whole-match
             median anchor, event-start ball proxy, models/mean_model.joblib
             (the shipped RF; it was trained on these matches, so this is if
             anything flattering to OLD), 6.5 m/s mu rate limit, anchors snapped
             to a 1 s grid with 1.5 m observation noise, old OU parameters.
  base / coupled / stage2   HistGradientBoosting on common.features sets,
             Kalman with anchors at exact times (common.smoother).
Training sources: B = Metrica + IDSSE (minus the held-out match), C = B + SkillCorner.

Outputs (training/v2_results/): lomo_errors.csv, lomo_by_role_gap.csv,
structure.csv, calibration.json, ou_params.json, and the shipped models.
"""
import argparse
import json
import os
import sys
import time
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from paths import MODEL_DIR
from common import ballpath, smoother
from common.features import FEATS_BASE, FEATS_COUPLED, FEATS_STAGE2, stage2_features
import build_rows_v2
import tracking_prep
import loader_metrica, loader_idsse, loader_skillcorner

OUT = os.path.join(HERE, "v2_results")
os.makedirs(OUT, exist_ok=True)
FULL = [("metrica", g) for g in loader_metrica.GAMES] + [("idsse", m) for m in loader_idsse.MATCH_IDS]
SK = [("skillcorner", m) for m in loader_skillcorner.MATCH_IDS]
FEATSETS = {"base": FEATS_BASE, "coupled": FEATS_COUPLED}
HGB = dict(max_iter=250, learning_rate=0.08, max_leaf_nodes=63, min_samples_leaf=200, l2_regularization=1.0,
           random_state=0)
TRAIN_SUBSAMPLE = 0.5
GAP_BINS = [0, 5, 10, 20, 30, 60, 120, 1e9]
GAP_LABELS = ["0-5s", "5-10s", "10-20s", "20-30s", "30-60s", "60-120s", "120s+"]
ROLE_NAME = {0: "GK", 1: "DEF", 2: "MID", 3: "FWD"}
OLD_OU = {"theta_x": 0.0247, "sigma_x": 2.058, "theta_y": 0.0311, "sigma_y": 1.658}


def rows_of(key):
    return build_rows_v2.get(*key)


def fit(frames, feats):
    df = pd.concat(frames, ignore_index=True)
    df = df[df["known"]]
    df = df.sample(frac=TRAIN_SUBSAMPLE, random_state=0) if TRAIN_SUBSAMPLE < 1 else df
    X = df[feats].values
    mx = HistGradientBoostingRegressor(**HGB).fit(X, df["dx"].values)
    my = HistGradientBoostingRegressor(**HGB).fit(X, df["dy"].values)
    return mx, my


def predict(models, df, feats):
    X = df[feats].values
    return df["base_x"].values + models[0].predict(X), df["base_y"].values + models[1].predict(X)


def add_stage2(df, mu_x):
    """stage-2 features for every (period, team) of one match from stage-1 mu_x."""
    df = df.copy(); df["_mu"] = mu_x
    parts = []
    for (period, team), sub in df.groupby(["period", "team"]):
        opp = df[(df["period"] == period) & (df["team"] != team)]
        parts.append(stage2_features(sub, opp, sub["_mu"].values, opp["_mu"].values))
    out = pd.concat(parts).loc[df.index]
    return out.drop(columns="_mu")


def reconstruct(df, mu_x, mu_y, sims, ou, hard_var=smoother.HARD_VAR, mu_sigma=smoother.MU_SMOOTH_S):
    """Kalman per player-period with the simulated anchors at exact times.
    Returns est_x, est_y, est_sd aligned with df, plus {(period,pid): (U,x,y)}."""
    est_x = np.empty(len(df)); est_y = np.empty(len(df)); est_sd = np.empty(len(df))
    tracks = {}
    df = df.assign(_mx=mu_x, _my=mu_y)
    for (period, pid), sub in df.groupby(["period", "pid"]):
        G = sub["t"].values.astype(float)
        A = sims[period]["anchors"].get(pid, np.zeros((0, 3)))
        mxs = smoother.smooth_mean(G, sub["_mx"].values, mu_sigma)
        mys = smoother.smooth_mean(G, sub["_my"].values, mu_sigma)
        obs = np.column_stack([A[:, 0], A[:, 1], A[:, 2], np.full(len(A), hard_var)]) if len(A) else np.zeros((0, 4))
        U, x, y, sd = smoother.smooth(G, mxs, mys, obs, ou)
        idx = np.searchsorted(U, np.round(G, 3))
        est_x[df.index.get_indexer(sub.index)] = x[idx]
        est_y[df.index.get_indexer(sub.index)] = y[idx]
        est_sd[df.index.get_indexer(sub.index)] = sd[idx]
        tracks[(period, pid)] = (U, x, y)
    return est_x, est_y, est_sd, tracks


def old_reconstruct(df, sims):
    """The previously shipped pipeline on the same simulated inputs."""
    sys.path.insert(0, os.path.join(ROOT, "tests", "baseline", "src_before", "training"))
    from kalman_ou import smooth_track as old_smooth
    model = joblib.load(os.path.join(MODEL_DIR, "mean_model.joblib"))
    est_x = np.empty(len(df)); est_y = np.empty(len(df))
    all_anchors = {}
    for period, s in sims.items():
        for pid, A in s["anchors"].items():
            if len(A):
                all_anchors.setdefault(pid, []).append(A)
    tracks = {}
    for (period, pid, team), sub in df.groupby(["period", "pid", "team"]):
        is_home = team == "Home"
        evs = sorted(sims[period]["ball_events"], key=lambda e: e["t0"])
        bt = np.array([e["t0"] for e in evs]); bxh = np.array([e["x0"] for e in evs]); byh = np.array([e["y0"] for e in evs])
        _, uniq = np.unique(bt, return_index=True)
        bt, bxh, byh = bt[uniq], bxh[uniq], byh[uniq]
        G = sub["t"].values.astype(float)
        A = np.vstack(all_anchors[pid]) if pid in all_anchors else np.zeros((0, 3))
        if len(A) < 2:
            est_x[df.index.get_indexer(sub.index)] = sub["base_x"].values
            est_y[df.index.get_indexer(sub.index)] = sub["base_y"].values
            continue
        ax, ay = np.median(A[:, 1]), np.median(A[:, 2])
        gbx, gby = np.interp(G, bt, bxh), np.interp(G, bt, byh)
        gbx, gby = ballpath.to_own_frame(gbx, gby, is_home)
        X = np.column_stack([np.full(len(G), ax), np.full(len(G), ay), gbx - ax, gby - ay, sub["poss"].values])
        pred = model.predict(X)
        mx = np.clip(ax + pred[:, 0], -3, 108); my = np.clip(ay + pred[:, 1], -3, 71)
        for i in range(1, len(G)):
            d = np.hypot(mx[i] - mx[i - 1], my[i] - my[i - 1]); lim = 6.5 * (G[i] - G[i - 1])
            if d > lim > 0:
                mx[i] = mx[i - 1] + (mx[i] - mx[i - 1]) * lim / d; my[i] = my[i - 1] + (my[i] - my[i - 1]) * lim / d
        Ap = sims[period]["anchors"].get(pid, np.zeros((0, 3)))
        kx, ky, _, _ = old_smooth(G, mx, my, Ap[:, 0], Ap[:, 1], Ap[:, 2], OLD_OU["theta_x"], OLD_OU["sigma_x"],
                                  OLD_OU["theta_y"], OLD_OU["sigma_y"], 2.25) if len(Ap) else (mx, my, 0, 0)
        est_x[df.index.get_indexer(sub.index)] = kx
        est_y[df.index.get_indexer(sub.index)] = ky
        tracks[(period, pid)] = (G, kx, ky)
    return est_x, est_y, tracks


def fit_ou(df, mu_x, mu_y):
    """OU theta/sigma per axis from out-of-fold residuals true - mu at consecutive seconds."""
    d = df.assign(rx=df["true_x"].values - mu_x, ry=df["true_y"].values - mu_y)
    d = d[d["known"]].sort_values(["key", "period", "pid", "t"])
    same = (d["pid"].values[1:] == d["pid"].values[:-1]) & (np.diff(d["t"].values) == 1.0) & \
           (d["key"].values[1:] == d["key"].values[:-1])
    out = {}
    for ax in ("x", "y"):
        r = d["r" + ax].values
        ac = np.corrcoef(r[:-1][same], r[1:][same])[0, 1]
        theta = -np.log(ac)
        out["theta_" + ax] = float(theta)
        out["sigma_" + ax] = float(np.std(r) * np.sqrt(2 * theta))
    return out


def gap_to_anchor(df, sims):
    g = np.full(len(df), 1e9)
    for (period, pid), sub in df.groupby(["period", "pid"]):
        A = sims[period]["anchors"].get(pid, np.zeros((0, 3)))
        if len(A):
            t = sub["t"].values
            i = np.clip(np.searchsorted(A[:, 0], t), 1, max(len(A) - 1, 1))
            gg = np.minimum(np.abs(A[np.minimum(i, len(A) - 1), 0] - t), np.abs(A[i - 1, 0] - t))
            g[df.index.get_indexer(sub.index)] = gg
    return g


def structure(df, xcol, ycol):
    """Team-shape statistics on the 1 Hz frames where at least 9 outfield players of
    BOTH teams are present (known truth rows only, so real and reconstruction use
    exactly the same frames)."""
    d = df[df["known"]][["key", "period", "t", "team", "pid", "role", "poss", "bx", xcol, ycol]].rename(columns={xcol: "X", ycol: "Y"})
    of = d[d["role"] != 0]
    g = of.groupby(["key", "period", "t", "team"])
    cnt = g["X"].transform("size")
    of = of[cnt >= 9]
    g = of.groupby(["key", "period", "t", "team"])
    agg = g.agg(length=("X", lambda v: v.max() - v.min()), width=("Y", lambda v: v.max() - v.min()),
                deepest=("X", "min"), front=("X", "max"), ball=("bx", "first"), poss=("poss", "first"))
    back = of.sort_values("X").groupby(["key", "period", "t", "team"]).head(4).groupby(["key", "period", "t", "team"])
    agg["back_x"] = back["X"].mean()
    agg["back_xspread"] = back["X"].agg(lambda v: v.max() - v.min())
    agg["back_yspread"] = back["Y"].agg(lambda v: v.max() - v.min())
    agg = agg.reset_index()
    other = agg[["key", "period", "t", "team", "deepest"]].copy()
    other["team"] = np.where(other["team"] == "Home", "Away", "Home")
    agg = agg.merge(other.rename(columns={"deepest": "opp_deepest"}), on=["key", "period", "t", "team"])
    agg["offside_line"] = 105.0 - agg["opp_deepest"]
    agg["att_minus_line"] = agg["front"] - agg["offside_line"]
    gk = d[d["role"] == 0].groupby(["key", "period", "t", "team"])["X"].mean().rename("gk_x").reset_index()
    agg = agg.merge(gk, on=["key", "period", "t", "team"], how="left")
    inposs = agg[agg["poss"] > 0.5]
    slope = np.polyfit(agg["ball"], agg["back_x"], 1)[0]
    return {
        "frames": int(len(agg)),
        "team_length_median": float(agg["length"].median()), "team_width_median": float(agg["width"].median()),
        "back_x_median": float(agg["back_x"].median()), "back_x_vs_ball_slope": float(slope),
        "back_x_vs_ball_corr": float(np.corrcoef(agg["ball"], agg["back_x"])[0, 1]),
        "back_line_xspread_median": float(agg["back_xspread"].median()),
        "back_line_yspread_median": float(agg["back_yspread"].median()),
        "gk_x_median": float(agg["gk_x"].median()),
        "share_attacker_beyond_last_defender_in_possession": float((inposs["att_minus_line"] > 0).mean()),
        "attacker_minus_line_median_in_possession": float(inposs["att_minus_line"].median()),
    }


def long_ball_motion(df, tracks, sims, key):
    """Median outfield displacement during simulated long balls (flight >= 2.5 s,
    >= 30 m), real vs reconstruction, and peak simultaneous team speed."""
    prep = tracking_prep.get(*key)
    real_d, rec_d = [], []
    for period, s in sims.items():
        P = prep[period]
        idx = {pid: i for i, pid in enumerate(P["pids"])}
        roles = P["role"]
        for e in s["ball_events"]:
            if e["t1"] - e["t0"] < 2.5 or np.hypot(e["x1"] - e["x0"], e["y1"] - e["y0"]) < 30:
                continue
            k0 = int(round((e["t0"] - P["T"][0]) * 5)); k1 = int(round((e["t1"] - P["T"][0]) * 5))
            if k1 >= len(P["T"]):
                continue
            rd, cd = [], []
            for pid, i in idx.items():
                if roles.get(pid) in (None, "GK") or not (P["known"][i, k0] and P["known"][i, k1]):
                    continue
                rd.append(np.hypot(*(P["own"][i, k1] - P["own"][i, k0])))
                tr = tracks.get((period, pid))
                if tr:
                    U, x, y = tr
                    cd.append(np.hypot(np.interp(e["t1"], U, x) - np.interp(e["t0"], U, x),
                                       np.interp(e["t1"], U, y) - np.interp(e["t0"], U, y)))
            if len(rd) >= 8 and len(cd) >= 8:
                real_d.append((np.median(rd), e["t1"] - e["t0"])); rec_d.append(np.median(cd))
    return real_d, rec_d


def team_speed(df, xcol, ycol):
    d = df[df["known"] & (df["role"] != 0)].sort_values(["key", "period", "pid", "t"])
    same = (d["pid"].values[1:] == d["pid"].values[:-1]) & (np.diff(d["t"].values) == 1.0)
    v = np.hypot(np.diff(d[xcol].values), np.diff(d[ycol].values))[same]
    fr = d.iloc[1:][same].assign(v=v).groupby(["key", "period", "t", "team"])["v"].agg(["mean", "size"])
    fr = fr[fr["size"] >= 8]["mean"]
    return float(fr.max()), float(fr.quantile(0.99)), float(fr.median())


def score(df, est_x, est_y, gap, label):
    e = np.hypot(est_x - df["true_x"].values, est_y - df["true_y"].values)
    k = df["known"].values
    out = pd.DataFrame({"err": e[k], "gap": gap[k], "role": df["role"].values[k].astype(int), "key": df["key"].values[k]})
    out["bucket"] = pd.cut(out["gap"], GAP_BINS, labels=GAP_LABELS, right=False)
    out["method"] = label
    return out


def run(configs, folds, featsets, do_old, do_stage2):
    data = {k: rows_of(k) for k in FULL + (SK if "C" in configs else [])}
    for k, v in data.items():
        v["rows"]["key"] = f"{k[0]}/{k[1]}"
    errs, structs, motion, speeds, oof = [], [], [], [], {}
    for held in folds:
        hd = data[held]["rows"]
        sims = data[held]["sims"]
        gap = gap_to_anchor(hd, sims)
        t0 = time.time()
        if do_old:
            ox, oy, otr = old_reconstruct(hd, sims)
            errs.append(score(hd, ox, oy, gap, "OLD"))
            structs.append({"held": held[1], "method": "OLD", **structure(hd.assign(ex=ox, ey=oy), "ex", "ey")})
            r, c = long_ball_motion(hd, otr, sims, held); motion += [("OLD", held[1], a[0], a[1], b) for a, b in zip(r, c)]
            speeds.append(("OLD", held[1], *team_speed(hd.assign(ex=ox, ey=oy), "ex", "ey")))
        structs.append({"held": held[1], "method": "REAL", **structure(hd, "true_x", "true_y")})
        speeds.append(("REAL", held[1], *team_speed(hd, "true_x", "true_y")))
        errs.append(score(hd, hd["base_x"].values, hd["base_y"].values, gap, "anchor_window_median"))
        errs.append(score(hd, hd["base_x"].values + hd["idx"].values, hd["base_y"].values + hd["idy"].values, gap, "anchor_interp"))
        for cfg in configs:
            train = [data[k]["rows"] for k in FULL if k != held] + ([data[k]["rows"] for k in SK] if cfg == "C" else [])
            for fs in featsets:
                models = fit(train, FEATSETS[fs])
                mx, my = predict(models, hd, FEATSETS[fs])
                name = f"{fs}_{cfg}"
                oof.setdefault(name, []).append((held, mx, my))
                errs.append(score(hd, mx, my, gap, name + "_mu"))
                if do_stage2 and fs == "coupled":
                    # stage 2 trained on out-of-fold stage-1 predictions of the training matches
                    s2_train = []
                    tr_keys = [k for k in FULL if k != held] + (SK if cfg == "C" else [])
                    for j, part in enumerate(np.array_split(np.arange(len(tr_keys)), 3)):
                        inner = [data[tr_keys[i]]["rows"] for i in range(len(tr_keys)) if i not in part]
                        m_in = fit(inner, FEATS_COUPLED)
                        for i in part:
                            d = data[tr_keys[i]]["rows"]
                            px_, _ = predict(m_in, d, FEATS_COUPLED)
                            s2_train.append(add_stage2(d, px_))
                    models2 = fit(s2_train, FEATS_STAGE2)
                    hd2 = add_stage2(hd, mx)
                    mx2, my2 = predict(models2, hd2, FEATS_STAGE2)
                    oof.setdefault(f"stage2_{cfg}", []).append((held, mx2, my2))
                    errs.append(score(hd, mx2, my2, gap, f"stage2_{cfg}_mu"))
            print(f"  fold {held[1]} cfg {cfg} done ({time.time()-t0:.0f}s)", flush=True)
        pd.concat(errs).to_pickle(os.path.join(OUT, "errs_partial.pkl"))
    # OU params per method from out-of-fold residuals, then Kalman on every fold
    ou_all, calib = {}, {}
    for name, lst in oof.items():
        big = pd.concat([data[h]["rows"] for h, _, _ in lst], ignore_index=True)
        mxa = np.concatenate([m for _, m, _ in lst]); mya = np.concatenate([m for _, _, m in lst])
        ou = fit_ou(big, mxa, mya); ou_all[name] = ou
        ratios = {"metrica": [], "idsse": []}
        for held, mx, my in lst:
            hd = data[held]["rows"]; sims = data[held]["sims"]
            gap = gap_to_anchor(hd, sims)
            ex, ey, esd, tr = reconstruct(hd, mx, my, sims, ou)
            errs.append(score(hd, ex, ey, gap, name + "_kalman"))
            structs.append({"held": held[1], "method": name, **structure(hd.assign(ex=ex, ey=ey), "ex", "ey")})
            r, c = long_ball_motion(hd, tr, sims, held); motion += [(name, held[1], a[0], a[1], b) for a, b in zip(r, c)]
            speeds.append((name, held[1], *team_speed(hd.assign(ex=ex, ey=ey), "ex", "ey")))
            pd.to_pickle({"key": held, "ex": ex, "ey": ey, "sd": esd, "mx": mx, "my": my},
                         os.path.join(OUT, f"oof_{name}_{held[0]}_{held[1]}.pkl"))
            k = hd["known"].values
            e = np.hypot(ex - hd["true_x"].values, ey - hd["true_y"].values)[k]
            ratios[held[0]].append(e / np.maximum(esd[k], 1e-3))
        rm = np.concatenate(ratios["metrica"] or [np.ones(1)]); ri = np.concatenate(ratios["idsse"] or [np.ones(1)])
        kq = {q: float(np.quantile(rm, q)) for q in (0.5, 0.68, 0.9)}
        calib[name] = {"fit_on": "metrica folds", "k50": kq[0.5], "k68": kq[0.68], "k90": kq[0.9],
                       "idsse_coverage_50": float((ri < kq[0.5]).mean()), "idsse_coverage_68": float((ri < kq[0.68]).mean()),
                       "idsse_coverage_90": float((ri < kq[0.9]).mean())}
    E = pd.concat(errs, ignore_index=True)
    E.to_pickle(os.path.join(OUT, "errs.pkl"))
    summ = E.groupby("method")["err"].agg(["median", "mean", lambda s: s.quantile(0.9), "size"])
    summ.columns = ["median", "mean", "p90", "n"]
    summ.round(3).to_csv(os.path.join(OUT, "lomo_errors.csv"))
    E.groupby(["method", "role", "bucket"])["err"].median().unstack("bucket").round(2).to_csv(os.path.join(OUT, "lomo_by_role_gap.csv"))
    E.groupby(["method", "key"])["err"].median().unstack("method").round(3).to_csv(os.path.join(OUT, "lomo_by_fold.csv"))
    pd.DataFrame(structs).to_csv(os.path.join(OUT, "structure.csv"), index=False)
    pd.DataFrame(motion, columns=["method", "held", "real_med_disp", "flight_s", "recon_med_disp"]).to_csv(os.path.join(OUT, "long_ball_motion.csv"), index=False)
    pd.DataFrame(speeds, columns=["method", "held", "team_speed_max", "team_speed_p99", "team_speed_median"]).to_csv(os.path.join(OUT, "team_speed.csv"), index=False)
    json.dump(ou_all, open(os.path.join(OUT, "ou_params.json"), "w"), indent=1)
    json.dump(calib, open(os.path.join(OUT, "calibration.json"), "w"), indent=1)
    print(summ.round(3).to_string())
    return E


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="*", default=["B", "C"])
    ap.add_argument("--featsets", nargs="*", default=["base", "coupled"])
    ap.add_argument("--folds", type=int, default=len(FULL))
    ap.add_argument("--no-old", action="store_true")
    ap.add_argument("--stage2", action="store_true")
    a = ap.parse_args()
    run(a.configs, FULL[: a.folds], a.featsets, not a.no_old, a.stage2)
