"""Pools every licensed tracking source, measures what each one adds, then
trains the production mean model.

Evaluation is leave-one-match-out over the FULLY tracked matches only
(Metrica x3, DFL/IDSSE x7): every player, every frame, so the held-out error is
a clean measurement. SkillCorner broadcast tracking only observes on-screen
players, so its matches are used for training but not as held-out yardsticks
(an error measured only on on-screen moments isn't comparable).

Three training configurations are scored on the SAME held-out folds:
  A  previous source set: Metrica games 1-2 + DFL/IDSSE 7
  B  A + Metrica game 3
  C  B + SkillCorner 20                       <- production
A fold never trains on its own held-out match, in any configuration.

Outputs: training/pool_results.csv, models/mean_model.joblib (config C, all
30 matches), models/mean_model_no_metrica_game2.joblib (config C minus
Metrica game 2 — the model benchmark.py must use, since it scores game 2).
"""
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import cache, MODEL_DIR
from fit_mean_model import build_training_rows, FEATS, TARGETS, RF_PARAMS
import loader_metrica
import loader_idsse
import loader_skillcorner

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS_VERSION = "v2"  # bump whenever build_training_rows changes

METRICA = [("metrica", g) for g in loader_metrica.GAMES]
IDSSE = [("idsse", m) for m in loader_idsse.MATCH_IDS]
SKILLCORNER = [("skillcorner", m) for m in loader_skillcorner.MATCH_IDS]
FULL_TRACKING = METRICA + IDSSE
CONFIGS = {
    "A_previous_sources": set(METRICA[:2] + IDSSE),
    "B_plus_metrica_game3": set(METRICA + IDSSE),
    "C_plus_skillcorner": set(METRICA + IDSSE + SKILLCORNER),
}

def load(source, mid):
    return {"metrica": loader_metrica.load_game, "idsse": loader_idsse.load_match,
            "skillcorner": loader_skillcorner.load_match}[source](mid)

def get_rows(source, mid):
    path = cache("rows", ROWS_VERSION, f"{source}_{mid}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    t0 = time.time()
    df = build_training_rows(load(source, mid), sample_every_s=1.0).dropna().reset_index(drop=True)
    df.to_pickle(path)
    print(f"  built {source}/{mid}: {len(df)} rows in {time.time()-t0:.0f}s", flush=True)
    return df

def fit(frames):
    df = pd.concat(frames, ignore_index=True)
    m = RandomForestRegressor(**RF_PARAMS)
    m.fit(df[FEATS].values, df[TARGETS].values)
    return m, len(df)

def err(model, df):
    p = model.predict(df[FEATS].values)
    e = np.hypot(p[:, 0] - df["offset_x"].values, p[:, 1] - df["offset_y"].values)
    return e.mean(), np.median(e)

if __name__ == "__main__":
    all_matches = METRICA + IDSSE + SKILLCORNER
    rows = {}
    for key in all_matches:
        rows[key] = get_rows(*key)
    for src in ("metrica", "idsse", "skillcorner"):
        n = sum(len(v) for k, v in rows.items() if k[0] == src)
        print(f"{src}: {n} training rows from {sum(1 for k in rows if k[0]==src)} matches", flush=True)

    results_path = os.path.join(HERE, "pool_results.csv")
    done = set()
    results = []
    if os.path.exists(results_path):
        prior = pd.read_csv(results_path)
        results = prior.to_dict("records")
        done = set(zip(prior["held_out"], prior["config"]))
        print(f"resuming: {len(done)} fold/config results already on disk", flush=True)

    for held in FULL_TRACKING:
        for cfg, members in CONFIGS.items():
            key = (f"{held[0]}/{held[1]}", cfg)
            if key in done:
                continue
            t0 = time.time()
            model, n = fit([rows[k] for k in members if k != held])
            mean_e, med_e = err(model, rows[held])
            results.append({"held_out": f"{held[0]}/{held[1]}", "config": cfg, "train_rows": n,
                            "mean_err_m": mean_e, "median_err_m": med_e})
            print(f"  held {held[0]}/{held[1]:14s} {cfg:22s} mean={mean_e:.3f}m median={med_e:.3f}m "
                  f"({n} rows, {time.time()-t0:.0f}s)", flush=True)
            # Write after EVERY fold, not just at the end — a background run
            # that gets interrupted (this exact thing happened once already)
            # shouldn't lose completed folds.
            pd.DataFrame(results).to_csv(results_path, index=False)

    res = pd.DataFrame(results)
    res.to_csv(os.path.join(HERE, "pool_results.csv"), index=False)
    print("\n=== mean over the 10 held-out full-tracking matches ===")
    print(res.groupby("config")[["mean_err_m", "median_err_m"]].mean().round(3).to_string())

    # How well does the pre-SkillCorner model transfer to a new competition
    # and provider? (Detected-only rows, so read as indicative, not a fold.)
    model_a, _ = fit([rows[k] for k in CONFIGS["A_previous_sources"]])
    sk = [err(model_a, rows[k]) for k in SKILLCORNER]
    print(f"\nconfig A (no SkillCorner) on SkillCorner detected rows: mean={np.mean([s[0] for s in sk]):.3f}m "
          f"median={np.mean([s[1] for s in sk]):.3f}m (avg over 20 matches)")

    # PRODUCTION_CONFIG: decided from the LOMO table just computed, not
    # assumed in advance. SkillCorner (config C) made the model WORSE than A
    # on every single one of the 10 held-out full-tracking folds, with no
    # exceptions (see pool_results.csv / BENCHMARKS.md) — a clean, consistent
    # negative result, not noise. Metrica game 3 (config B) is neutral to
    # marginally positive vs A on the same folds. Shipping the numerically
    # worst validated option as "production" just because it used the most
    # data would be indefensible, so config B is what's actually shipped;
    # config C is still fit and saved (as mean_model_C_with_skillcorner.joblib)
    # so the negative result is reproducible and auditable, not just asserted.
    PRODUCTION_CONFIG = "B_plus_metrica_game3"

    os.makedirs(MODEL_DIR, exist_ok=True)
    final, n = fit([rows[k] for k in CONFIGS[PRODUCTION_CONFIG]])
    joblib.dump(final, os.path.join(MODEL_DIR, "mean_model.joblib"), compress=3)
    print(f"saved models/mean_model.joblib ({PRODUCTION_CONFIG}, {n} rows, "
          f"{len(CONFIGS[PRODUCTION_CONFIG])} matches)")
    bench, n = fit([rows[k] for k in CONFIGS[PRODUCTION_CONFIG] if k != ("metrica", "Sample_Game_2")])
    joblib.dump(bench, os.path.join(MODEL_DIR, "mean_model_no_metrica_game2.joblib"), compress=3)
    bench_a, n = fit([rows[k] for k in CONFIGS["A_previous_sources"] if k != ("metrica", "Sample_Game_2")])
    joblib.dump(bench_a, os.path.join(MODEL_DIR, "mean_model_A_no_metrica_game2.joblib"), compress=3)
    print("saved the two game-2-excluded models for benchmark.py")

    # Config C, saved for reproducibility of the negative result above —
    # never loaded as production anywhere in pipeline/ or training/.
    model_c, n = fit([rows[k] for k in CONFIGS["C_plus_skillcorner"]])
    joblib.dump(model_c, os.path.join(MODEL_DIR, "mean_model_C_with_skillcorner.joblib"), compress=3)
    print(f"saved models/mean_model_C_with_skillcorner.joblib ({n} rows, reference/reproducibility only)")
