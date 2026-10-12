"""Shot-type labelling: starter rules, replaced by a model trained on YOUR reviewed shots."""
from pathlib import Path

import numpy as np

SHOT_TYPES = ["serve", "return", "dink", "drop", "drive", "lob", "reset", "speedup", "overhead", "other"]

FEATURES = ["dist_from_net_ft", "lateral_from_center_ft", "landing_from_net_ft", "landing_known",
            "landed_in_kitchen", "speed_fts", "flight_s", "shot_in_rally", "volley",
            "contact_height", "hip_drop_pct", "elbow_angle", "forehand"]


def features(h):
    m = h.get("metrics", {})

    def g(k, default=-1.0):
        v = m.get(k)
        return default if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
    return [
        g("dist_from_net_ft"), g("lateral_from_center_ft", 0.0), g("landing_from_net_ft"),
        1.0 if h.get("landing_xy") else 0.0, 1.0 if m.get("landed_in_kitchen") else 0.0,
        g("speed_fts"), g("flight_s"), float(min(h.get("shot_in_rally", 3), 3)),
        1.0 if h.get("volley") else 0.0, g("contact_height", 0.5), g("hip_drop_pct", 0.0),
        g("elbow_angle", 120.0), 1.0 if m.get("wing") == "forehand" else 0.0,
    ]


def rule_based(h, thr):
    m = h.get("metrics", {})
    dn = m.get("dist_from_net_ft")
    speed = m.get("speed_fts")
    land = m.get("landing_from_net_ft")
    kitchen = m.get("landed_in_kitchen")
    ch = m.get("contact_height")
    idx = h.get("shot_in_rally", 99)
    if idx == 0 and (dn is None or dn >= thr["baseline_ft"]):
        return "serve"
    if idx == 1 and (dn is None or dn >= thr["baseline_ft"]):
        return "return"
    if ch is not None and not np.isnan(ch) and ch >= thr["overhead_contact_height"]:
        return "overhead"
    at_kitchen = dn is not None and dn <= thr["kitchen_zone_ft"]
    fast = speed is not None and speed >= thr["drive_speed_fts"]
    if at_kitchen:
        if fast:
            return "speedup"
        if land is not None and land >= thr["lob_depth_ft"] and (speed or 0) < thr["drive_speed_fts"]:
            return "lob"
        if kitchen or (speed is not None and speed < thr["dink_speed_fts"]):
            return "dink"
        return "other"
    if fast:
        return "drive"
    if kitchen or (land is not None and land <= thr["drop_land_ft"]):
        return "drop" if dn is not None and dn >= thr["baseline_ft"] - 4 else "reset"
    if land is not None and land >= thr["lob_depth_ft"] and (speed or 99) < thr["dink_speed_fts"] * 1.5:
        return "lob"
    return "other"


class ShotClassifier:
    def __init__(self, model_path, thresholds):
        self.thr = thresholds
        self.model = None
        p = Path(model_path)
        if p.exists():
            import joblib
            self.model = joblib.load(p)

    @property
    def mode(self):
        return "learned" if self.model is not None else "rules"

    def predict(self, hits):
        if not hits:
            return
        if self.model is not None:
            X = np.array([features(h) for h in hits])
            preds = self.model.predict(X)
            probs = self.model.predict_proba(X).max(axis=1)
            for h, p, c in zip(hits, preds, probs):
                h["type_auto"], h["type_conf"] = str(p), round(float(c), 2)
        else:
            for h in hits:
                h["type_auto"], h["type_conf"] = rule_based(h, self.thr), None


def train(shot_files, model_path, min_labels=30):
    """Train a random forest on every hit you've labelled in the review tool."""
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    from .session import load_shots

    X, y = [], []
    for f in shot_files:
        for h in load_shots(f)["hits"]:
            if h.get("deleted") or not h.get("type"):
                continue
            X.append(features(h))
            y.append(h["type"])
    print(f"Found {len(y)} labelled shots across {len(shot_files)} session(s).")
    if len(y) < min_labels:
        print(f"Need at least {min_labels} labelled shots to train (label more in `review`).")
        return None
    X, y = np.array(X), np.array(y)
    clf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced", random_state=0)
    counts = {str(c): int((y == c).sum()) for c in set(y)}
    if min(counts.values()) >= 3 and len(counts) > 1:
        cv = min(5, min(counts.values()))
        acc = cross_val_score(clf, X, y, cv=cv).mean()
        print(f"Cross-validated accuracy: {acc:.0%}  (label counts: {counts})")
    clf.fit(X, y)
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, model_path)
    imp = sorted(zip(clf.feature_importances_, FEATURES), reverse=True)[:5]
    print("Most useful features: " + ", ".join(f"{n} ({v:.2f})" for v, n in imp))
    print(f"Saved model -> {model_path}")
    return model_path
