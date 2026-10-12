"""Setup-check test: a good synthetic tee session should pass; broken setups should be caught.

    python tests/setup_check_test.py
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pb.tee as tee  # noqa: E402
from pb.setup_check import check_setup  # noqa: E402
from tee_test import OUT, make_session  # noqa: E402


def status(ck, title):
    return next((i["status"] for i in ck.items if i["title"] == title), None)


def main():
    silent, video, evs, cfg = make_session()  # also installs the synthetic pose model
    sess = OUT / "setup_check"
    results = []

    ck = check_setup(video, cfg, sess, sess / "good.html")
    results.append(("good setup passes", ck.verdict == "pass"))

    ck = check_setup(silent, cfg, sess, sess / "silent.html")
    results.append(("no sound fails", ck.verdict == "fail" and status(ck, "Sound") == "fail"))

    wrong = copy.deepcopy(cfg)
    wrong["tee"]["camera_height_ft"] = 5.0  # settings don't match where the camera really is
    ck = check_setup(video, wrong, sess, sess / "wrong_height.html")
    results.append(("wrong camera height warns", status(ck, "Camera height & distance") == "warn"))

    far = copy.deepcopy(cfg)
    far["tee"]["board_distance_ft"] = 40  # board pops can't pair with hits at this distance
    ck = check_setup(video, far, sess, sess / "board.html")
    results.append(("wrong board distance warns", status(ck, "Board pops") == "warn"))

    real_pose = tee.run_pose
    tee.run_pose = lambda *a: {}
    ck = check_setup(video, cfg, sess, sess / "no_pose.html")
    tee.run_pose = real_pose
    results.append(("missing pose model is skipped, not failed", status(ck, "Body in frame") == "skip" and ck.verdict == "pass"))

    print()
    for label, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    assert all(ok for _, ok in results), "setup check regressed"
    print("OK")


if __name__ == "__main__":
    main()
