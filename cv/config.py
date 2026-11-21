"""Detector thresholds and model settings. Tune here, not in code."""

# Pose model: preferred weights first, fallback second. Cached under local/weights/.
MODEL_CANDIDATES = ("yolo26n-pose.pt", "yolo11n-pose.pt")
WEIGHTS_DIR = "local/weights"
INFERENCE_IMGSZ = 640
PERSON_MIN_CONF = 0.25  # person box confidence passed to the model

# COCO keypoint indices used by the torso rule.
L_SHOULDER, R_SHOULDER = 5, 6
L_HIP, R_HIP = 11, 12
TORSO_KEYPOINTS = (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)
KEYPOINT_MIN_CONF = 0.5  # all four torso points must clear this, else UNKNOWN

# Posture rule (used by the detector state machine, shown on the overlay now).
DOWN_MAX_TORSO_DEG = 30.0  # torso within this many degrees of horizontal counts as "down"
CONFIRM_DOWN_SECONDS = 2.0
REARM_UPRIGHT_SECONDS = 2.0
