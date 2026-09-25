"""Hide adjacent cards outside an observed compact reward card's outline."""

import cv2
import numpy as np


def isolate_compact_card(image):
    """Add transparency around a complete white frame, keeping all BGR pixels.

    Slanted cards overlap one another's rectangular bounding boxes. Their bright
    outer frame encloses arbitrary artwork, so its convex hull supplies the mask
    without relying on a sprite or fixed slant. A broken or ambiguous frame keeps
    the original crop. RGB data is untouched even where alpha is zero: receipt
    comparisons intentionally decode PNGs as BGR and must retain their existing
    sensitivity to every source pixel.
    """
    if image.ndim != 3 or image.shape[2] != 3 or not image.size:
        return image
    height, width = image.shape[:2]
    if not (40 <= width <= 130 and 40 <= height <= 110):
        return image
    white = (image.min(axis=2) > 225).astype(np.uint8) * 255
    contours = cv2.findContours(
        white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )[0]
    if not contours:
        return image
    outline = cv2.convexHull(max(contours, key=cv2.contourArea))
    x, y, w, h = cv2.boundingRect(outline)
    coverage = cv2.contourArea(outline) / (width * height)
    # A near-rectangular hull can include the background and neighboring frames;
    # a short/narrow hull may be an incomplete border or white artwork instead.
    if not (
        .72 <= coverage <= .93
        and x <= width * .06
        and y <= height * .08
        and x + w >= width * .94
        and y + h >= height * .94
    ):
        return image
    alpha = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(alpha, outline, 255)
    # Keep the antialiased outer frame and its immediate shadow as well.
    alpha = cv2.dilate(alpha, np.ones((3, 3), dtype=np.uint8))
    result = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    result[:, :, 3] = alpha
    return result
