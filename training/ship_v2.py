"""Fits the chosen configuration on ALL of its training matches and writes
models/v2_bundle.joblib (models + feature set + OU params + calibration), taking
OU params and calibration from the LOMO run (training/v2_results/)."""
import argparse
import json
import os
import sys

import joblib
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from paths import MODEL_DIR
import train_v2
from common.features import FEATS_COUPLED, FEATS_STAGE2

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--featset", default="base", choices=["base", "coupled", "stage2"])
    ap.add_argument("--config", default="B", choices=["B", "C"])
    a = ap.parse_args()
    keys = train_v2.FULL + (train_v2.SK if a.config == "C" else [])
    data = {k: train_v2.rows_of(k)["rows"] for k in keys}
    name = f"{a.featset}_{a.config}"
    ou = json.load(open(os.path.join(train_v2.OUT, "ou_params.json")))[name]
    cal = json.load(open(os.path.join(train_v2.OUT, "calibration.json")))[name]
    models = {}
    if a.featset == "stage2":
        models["stage1"] = train_v2.fit(list(data.values()), FEATS_COUPLED)
        s2 = []
        for j, part in enumerate(np.array_split(np.arange(len(keys)), 3)):
            inner = train_v2.fit([data[keys[i]] for i in range(len(keys)) if i not in part], FEATS_COUPLED)
            for i in part:
                d = data[keys[i]]
                px, _ = train_v2.predict(inner, d, FEATS_COUPLED)
                s2.append(train_v2.add_stage2(d, px))
        models["main"] = train_v2.fit(s2, FEATS_STAGE2)
    else:
        models["main"] = train_v2.fit(list(data.values()), train_v2.FEATSETS[a.featset])
    bundle = {"name": name, "featset": a.featset, "config": a.config, "models": models, "ou": ou,
              "calibration": {"k50": cal["k50"], "k68": cal["k68"], "k90": cal["k90"]},
              "training_matches": [f"{s}/{m}" for s, m in keys]}
    joblib.dump(bundle, os.path.join(MODEL_DIR, "v2_bundle.joblib"), compress=3)
    print("saved models/v2_bundle.joblib", name, ou, bundle["calibration"])
