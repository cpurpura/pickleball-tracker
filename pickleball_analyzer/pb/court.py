"""Court geometry and image <-> court (feet) mapping.

Court coordinate system (feet), viewed from above:
    x: 0 (left sideline) .. 20 (right sideline)   -- "left" as seen from the camera
    y: 0 (near baseline, closest to camera) .. 44 (far baseline)
    Net at y = 22. Kitchen (non-volley zone) lines at y = 15 and y = 29.
"""
import json
import cv2
import numpy as np

COURT_W = 20.0
COURT_L = 44.0
NET_Y = 22.0
KITCHEN_DEPTH = 7.0
NEAR_KITCHEN_Y = NET_Y - KITCHEN_DEPTH  # 15
FAR_KITCHEN_Y = NET_Y + KITCHEN_DEPTH   # 29

# Order the user clicks the corners in during calibration.
CORNER_NAMES = [
    "NEAR-LEFT baseline corner",
    "NEAR-RIGHT baseline corner",
    "FAR-RIGHT baseline corner",
    "FAR-LEFT baseline corner",
]
CORNER_COURT = np.array([[0, 0], [COURT_W, 0], [COURT_W, COURT_L], [0, COURT_L]], dtype=np.float32)


class Court:
    def __init__(self, image_corners):
        self.image_corners = np.asarray(image_corners, dtype=np.float32)
        self.H, _ = cv2.findHomography(self.image_corners, CORNER_COURT)
        self.H_inv = np.linalg.inv(self.H)

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f)["image_corners"])

    def save(self, path):
        with open(path, "w") as f:
            json.dump({"image_corners": self.image_corners.tolist()}, f, indent=2)

    # ---- mapping -----------------------------------------------------
    def to_court(self, pts):
        """Image pixels (N,2) -> court feet (N,2). Only valid for points ON the ground."""
        pts = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H).reshape(-1, 2)

    def to_image(self, pts):
        pts = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H_inv).reshape(-1, 2)

    # ---- helpers -----------------------------------------------------
    @staticmethod
    def side(court_y):
        return "near" if court_y < NET_Y else "far"

    @staticmethod
    def dist_from_net(court_y):
        return abs(court_y - NET_Y)

    @staticmethod
    def in_bounds(court_xy, margin=0.0):
        x, y = court_xy
        return -margin <= x <= COURT_W + margin and -margin <= y <= COURT_L + margin

    def draw_lines(self, img, color=(255, 255, 255), thickness=2):
        """Overlay the court lines (useful to verify calibration)."""
        segs = [
            ((0, 0), (COURT_W, 0)), ((COURT_W, 0), (COURT_W, COURT_L)),
            ((COURT_W, COURT_L), (0, COURT_L)), ((0, COURT_L), (0, 0)),
            ((0, NET_Y), (COURT_W, NET_Y)),
            ((0, NEAR_KITCHEN_Y), (COURT_W, NEAR_KITCHEN_Y)),
            ((0, FAR_KITCHEN_Y), (COURT_W, FAR_KITCHEN_Y)),
            ((COURT_W / 2, 0), (COURT_W / 2, NEAR_KITCHEN_Y)),
            ((COURT_W / 2, FAR_KITCHEN_Y), (COURT_W / 2, COURT_L)),
        ]
        for a, b in segs:
            p = self.to_image([a, b]).astype(int)
            cv2.line(img, tuple(p[0]), tuple(p[1]), color, thickness, cv2.LINE_AA)
        return img


def draw_court_mpl(ax, flip_far=False):
    """Draw a top-down court on a matplotlib axis (feet)."""
    import matplotlib.patches as patches
    ax.add_patch(patches.Rectangle((0, 0), COURT_W, COURT_L, fill=True, fc="#2f6f4f", ec="white", lw=2))
    ax.add_patch(patches.Rectangle((0, NEAR_KITCHEN_Y), COURT_W, 2 * KITCHEN_DEPTH, fill=True, fc="#3d8a63", ec="white", lw=1.5))
    ax.plot([0, COURT_W], [NET_Y, NET_Y], color="#dddddd", lw=3)
    ax.plot([COURT_W / 2] * 2, [0, NEAR_KITCHEN_Y], color="white", lw=1.5)
    ax.plot([COURT_W / 2] * 2, [FAR_KITCHEN_Y, COURT_L], color="white", lw=1.5)
    ax.set_xlim(-4, COURT_W + 4)
    ax.set_ylim(-4, COURT_L + 4)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor("#1e3d2d")
