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
# Rearm counts only clearly upright frames. Upright needs ALL of:
#   torso at least UPRIGHT_MIN_TORSO_DEG steep, shoulders above hips (not head-down),
#   and hips raised above the person's own feet: vertical hip->ankle gap at least
#   UPRIGHT_MIN_HIP_ANKLE_RATIO x torso length (hip->knee gap and its ratio if ankles
#   aren't visible; no leg check if neither is visible). Sitting up on the floor fails
#   the leg check, so it does not rearm. Frames that are neither down nor upright reset
#   the rearm timer.
UPRIGHT_MIN_TORSO_DEG = 60.0
UPRIGHT_MIN_HIP_ANKLE_RATIO = 0.6
UPRIGHT_MIN_HIP_KNEE_RATIO = 0.3

# More than one person: if anyone is down, the frame is DOWN (that person is tracked).
# Otherwise two or more people with box confidence >= this make the frame UNKNOWN
# (multiple_people), so a bystander or responder can't rearm or reset the detector.
# Weaker detections (e.g. a reflection in a TV) don't count toward "multiple".
OTHER_PERSON_MIN_CONF = 0.5

# Alert event (fields match fixtures/person-down.json).
ALERT_SIGNAL = "possible_person_down"  # CLAUDE.md; the synthetic fixture says "person_down"
CAMERA_ID = "demo-camera"
ZONE_ID = "demo-area"

# State machine timings, in seconds of frame timestamps (not frame counts).
CONFIRM_DOWN_SECONDS = 2.0
REARM_UPRIGHT_SECONDS = 2.0
MAX_FRAME_GAP_SECONDS = 0.5  # a longer gap between frames breaks pending timers like UNKNOWN
