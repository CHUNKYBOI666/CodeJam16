# Fixed-Camera Kitchen Location Design

## Goal

Show an approximate top-down position for a simulated or detected person on the visible kitchen floor. Use relative coordinates and show the detected position as a red dot on the responder minimap.

## Camera position

Use the fixed overhead iPhone view tested at 1920 × 1080. The visible left wall and right-side chair orient the map. Use those same sides in the capture and the viewer. Check that shoulders and hips are visible while down and that a clearly upright pose can rearm the detector.

## Use the floor as the reference

Choose four visible floor-level points that form a 3:2 rectangle across the open floor. A known floor pattern or tape points can establish the proportions without measurements in metres. Start at the upper-left of the overhead frame (A), then upper-right (B), lower-right (C), and lower-left (D). Project the corners to the unit square and draw the minimap in the rectangle's proportions.

Check the projection using an independent interior floor point. The stable frame has one tape mark near the center that can serve as a check. Keep the exact coverage provisional until A–D are verified.

## Draw the minimap from the calibrated area

Draw the wall as a line to the left of the covered area and the chair as a small landmark to its right. Use unlabeled landmarks to keep the viewer oriented in the same direction as the iPhone frame. Let the red dot convey the location in the alert and check row.

In simulation mode, clicking the source frame projects a sample pin and leaves the hosted database untouched. In live mode, use the midpoint between the selected person's shoulder and hip midpoints on the confirming frame as an estimate. Render a pin only inside the calibrated area and keep the plain alert label when a pin cannot be placed.

## Acceptance

Choose two floor spots well apart, one toward the wall and one toward the chair. Preview both with clicks, then rehearse with the fixed camera and the existing detector. The map should show the right zone and order for both spots, and the detector should still confirm each staged episode and rearm between them. If the phone shifts or its framing changes, realign the calibration before filming. Camera metadata alone cannot detect physical movement.
