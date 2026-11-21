"""Detector thresholds and model settings. Tune here, not in code."""

# Pose model: preferred weights first, fallback second. Cached under local/weights/.
MODEL_CANDIDATES = ("yolo26n-pose.pt", "yolo11n-pose.pt")
WEIGHTS_DIR = "local/weights"
INFERENCE_IMGSZ = 640
PERSON_MIN_CONF = 0.25  # person box confidence passed to the model
WARMUP_PASSES = 3  # dummy inferences at the source frame size before capture starts

# Floor region calibration (polygon clicked on the first frame).
CALIBRATION_PATH = "local/calibration.json"

# COCO keypoint indices used by the torso rule.
L_SHOULDER, R_SHOULDER = 5, 6
L_HIP, R_HIP = 11, 12
TORSO_KEYPOINTS = (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)
KEYPOINT_MIN_CONF = 0.5  # all four torso points must clear this, else UNKNOWN

# Posture rule. "Down" needs ALL of:
#   torso within DOWN_MAX_TORSO_DEG of horizontal,
#   shoulder-mid and hip-mid inside the calibrated floor polygon,
#   keypoint bounding box wider than tall (width / height > BBOX_MIN_ASPECT).
DOWN_MAX_TORSO_DEG = 30.0
BBOX_MIN_ASPECT = 1.0
# Rearm counts only clearly upright frames (torso at least this steep). Frames that are
# neither down nor upright (e.g. half sitting up on the floor) reset the rearm timer.
UPRIGHT_MIN_TORSO_DEG = 60.0

# State machine timings, in seconds of frame timestamps (not frame counts).
CONFIRM_DOWN_SECONDS = 2.0
REARM_UPRIGHT_SECONDS = 2.0
MAX_FRAME_GAP_SECONDS = 0.5  # a longer gap between frames breaks pending timers like UNKNOWN
