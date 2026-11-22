# Fixed-Camera Kitchen Location Design

Written 22 Nov 2026. Planning artifact for the hosted demo on merged `main` (`acdefe3`).

## Intent

When the fixed kitchen camera confirms a possible person down, the hosted check should show an approximate position on a simple top-down kitchen layout. Responders should see a pin and a useful place label, such as “Near the counter.” The video and pose overlay stay on the Mac.

## Decisions and approaches

The user chose **one-time manual calibration**. With the camera mounted in its filming position, the operator identifies matching fixed, floor-level points in a camera frame and the 2D layout. The result is saved locally and loaded at every launch. Moving or reframing the camera requires recalibration.

| Approach | Trade-off |
| --- | --- |
| One-time point correspondence and planar homography **(chosen)** | Small setup task, deterministic, no model training. Works while the camera and kitchen stay fixed. |
| Automatic landmark matching at each launch | Needs reliable recognition and identity matching of the same physical counter ends and other fixtures. More code and rehearsal risk for this fixed-camera demo. |
| Hand-assigned broad zones | Easy to wire but cannot put a meaningful pin on a measured 2D layout. |

The counter and other fixed kitchen features define the layout and give recognizable calibration references. Points used to solve the camera-to-map transform must lie on the **floor plane**: counter base corners, tile intersections, and a doorway threshold work; the top of a counter does not. Record at least five well-spread, non-collinear correspondences, using at least four to fit and a separate point to check accuracy. Visible permanent features are preferred; temporary floor marks may be used during setup if natural points are hard to click precisely.

## Data flow

1. Store the measured kitchen footprint, counter footprint, landmark names and floor coordinates in one versioned layout file, in metres. The same file is read by the calibration tool and hosted frontend. The actual layout measurements and camera frame must come from the demo kitchen before final calibration.
2. A local calibration tool pauses on a camera frame, asks the operator to click each configured floor landmark, calculates an image-to-floor homography, checks the held-out point and saves the camera ID, source aspect ratio, layout version, points and matrix to `local/`. It never uploads the frame.
3. On a confirmed down episode, use the center of the detected person’s shoulder and hip midpoints as an approximate floor contact location. The existing detector already chooses the down person for the event. Project that image point into kitchen metres. Only attach coordinates when calibration is valid and the point is inside the mapped walkable floor. Otherwise, emit the original event without coordinates.
4. Add optional `map_x_m`, `map_y_m`, `location_label`, and `layout_version` fields to the check. `create_check` stores them on first insertion and retains them on duplicate `episode_id`. Existing checks and mock events without location continue to work.
5. Show the static kitchen outline, the counter and a pin in the hosted check row and alert takeover. Display the stored label alongside it. If no coordinates exist, show the existing `place` text without an empty map.

OpenCV’s `findHomography` computes the projective transform and `perspectiveTransform` applies it. The map pin is an estimate, because pose keypoints are not exact floor contacts. No per-frame cloud calls, new recognition model, or raw imagery are required.

## Boundaries and acceptance

- Python 3.12 and existing pinned OpenCV/Numpy versions; static Vite frontend and hosted Supabase Postgres remain the stack.
- Calibration is local and specific to one camera position, orientation and image crop. Different image resolutions with the same aspect ratio can rescale normalized image points; rotation or a different crop invalidates the calibration.
- A bad or missing calibration produces a plain place label and a visible local warning. It must never create a misleading map pin.
- Keep frames, reference stills, local calibration data and camera recordings out of Git. Commit only the kitchen layout geometry and tests.
- For the filmed demo, rehearse two different floor positions with a fixed camera. In each case the pin should land within approximately 1 metre of the measured position and on the correct side of the counter. Rearm the detector with at least 2 continuous seconds of clearly upright posture between episodes. The two checks should appear on joined phones and retain their pins through refresh and claim.
- If the counter hides the person’s torso or the camera misses the floor landmarks, reposition the camera before calibrating. The math cannot recover a floor location from an invisible person.

## Inputs still needed at rehearsal

- A rough top-down kitchen sketch with room and counter dimensions in metres, including the locations of five or more visible floor-level landmarks.
- A camera still from the final mounted position and two known person-down rehearsal spots for validation.

These are setup inputs, not reasons to block the code plan. The implementation should include a documented procedure for filling them in once the kitchen is available.
