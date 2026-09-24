"""Measure Cafe camera movement from scene features, without furniture templates.

The result is the dominant scene displacement in screenshot pixels (after minus
before). Animated students and occlusions may remove matches; they never imply
that the camera stopped. Insufficient or conflicting evidence returns None.
"""

from __future__ import annotations

import cv2
import numpy as np


SCENE_BOUNDS = (140, 135, 1150, 575)
MAX_FEATURES = 1800
MIN_MATCHES = 20
MAX_HYPOTHESES = 12


def _scene_gray(frame: bytes | np.ndarray) -> np.ndarray | None:
    if isinstance(frame, bytes):
        if not frame:
            return None
        try:
            frame = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error:
            return None
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.shape[:2] != (720, 1280)):
        return None
    left, top, right, bottom = SCENE_BOUNDS
    scene = frame[top:bottom, left:right]
    if scene.ndim == 2:
        return np.ascontiguousarray(scene)
    if scene.ndim != 3 or scene.shape[2] not in (3, 4):
        return None
    return cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY if scene.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)


def _distinct_matches(pairs) -> dict:
    # Requiring a distinct second-best match avoids repeated floor/wall patterns.
    return {best.queryIdx: best for pair in pairs if len(pair) == 2
            for best, second in [pair]
            if best.distance <= 64 and best.distance < 0.75 * second.distance}


def _tolerance(displacement: np.ndarray) -> float:
    # Cafe camera drags have slight perspective/parallax, especially over tall
    # furniture. Allow that during a large pan, but require tight stationary proof.
    return max(2.5, min(32.0, float(np.linalg.norm(displacement)) * 0.085))


def _spread(points: np.ndarray, displacement: np.ndarray, *, destination: bool = False) -> bool:
    """One animated sprite or small patch cannot establish room-wide movement."""
    extent = np.array([SCENE_BOUNDS[2] - SCENE_BOUNDS[0], SCENE_BOUNDS[3] - SCENE_BOUNDS[1]])
    lower = np.maximum(0, -displacement)
    upper = np.minimum(extent, extent - displacement)
    width, height = upper - lower
    if width < 160 or height < 65:
        return False
    if destination:
        lower = lower + displacement
    span = np.ptp(points, axis=0)
    if span[0] < max(160, width * 0.28) or span[1] < max(60, height * 0.22):
        return False
    # ORB can describe the same corner at multiple pyramid levels. Count distinct
    # locations as well as descriptors, then require coverage over the whole crop.
    if len(np.unique(np.floor(points / 8).astype(np.int32), axis=0)) < 14:
        return False
    cells = np.floor((points - lower) / np.array([width / 4, height / 3])).astype(np.int32)
    if (len(np.unique(cells, axis=0)) < 5 or len(np.unique(cells[:, 0])) < 2
            or len(np.unique(cells[:, 1])) < 2):
        return False
    hull = cv2.convexHull(points.astype(np.float32))
    return cv2.contourArea(hull) >= width * height * 0.045


def measure_camera_displacement(
    before: bytes | np.ndarray,
    after: bytes | np.ndarray,
) -> tuple[float, float] | None:
    """Return scene (dx, dy), or None when camera movement is not established.

    Inputs are PNG bytes or uint8 grayscale/BGR/BGRA 1280×720 frames. HUD pixels
    outside SCENE_BOUNDS never participate. Sparse, localized, unrelated, and
    competing motion estimates fail closed. In particular, None is not (0, 0).

    Work is bounded to 1,800 ORB descriptors per frame and 13 deterministic
    translation hypotheses. No global OpenCV RNG/thread settings are modified.
    """
    first, second = _scene_gray(before), _scene_gray(after)
    if first is None or second is None:
        return None
    detector = cv2.ORB_create(nfeatures=MAX_FEATURES, scaleFactor=1.2, nlevels=8,
                             edgeThreshold=19, fastThreshold=12)
    first_points, first_descriptors = detector.detectAndCompute(first, None)
    second_points, second_descriptors = detector.detectAndCompute(second, None)
    if (first_descriptors is None or second_descriptors is None
            or len(first_points) < MIN_MATCHES or len(second_points) < MIN_MATCHES):
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    forward = _distinct_matches(matcher.knnMatch(first_descriptors, second_descriptors, k=2))
    backward = _distinct_matches(matcher.knnMatch(second_descriptors, first_descriptors, k=2))
    matches = [match for match in forward.values()
               if match.trainIdx in backward and backward[match.trainIdx].trainIdx == match.queryIdx]
    if len(matches) < MIN_MATCHES:
        return None
    source = np.array([first_points[match.queryIdx].pt for match in matches], dtype=np.float32)
    destination = np.array([second_points[match.trainIdx].pt for match in matches], dtype=np.float32)
    deltas = destination - source

    # Translation-only consensus is deterministic and cheaper than repeatedly
    # sampling affine transforms. It tolerates moving students without assuming a
    # particular room layout. Binned candidates avoid fitting an average of two
    # separate motions, which could otherwise masquerade as a stopped camera.
    bins = np.rint(deltas / 4).astype(np.int32)
    unique, counts = np.unique(bins, axis=0, return_counts=True)
    order = np.argsort(-counts, kind="stable")[:MAX_HYPOTHESES]
    seeds = [np.median(deltas, axis=0)]
    seeds.extend(np.median(deltas[np.all(bins == unique[index], axis=1)], axis=0) for index in order)
    models = []
    for seed in seeds:
        center = seed
        for _ in range(3):
            inliers = np.linalg.norm(deltas - center, axis=1) <= _tolerance(center)
            if int(inliers.sum()) < 6:
                break
            center = np.median(deltas[inliers], axis=0)
        inliers = np.linalg.norm(deltas - center, axis=1) <= _tolerance(center)
        count = int(inliers.sum())
        if count < 6:
            continue
        residual = float(np.median(np.linalg.norm(deltas[inliers] - center, axis=1)))
        models.append((count, residual, center, inliers))
    if not models:
        return None
    models.sort(key=lambda model: (-model[0], model[1]))
    count, _, center, inliers = models[0]
    stationary = float(np.linalg.norm(center)) <= 4.0
    if count < MIN_MATCHES or count / len(matches) < (0.75 if stationary else 0.60):
        return None
    if (not _spread(source[inliers], center)
            or not _spread(destination[inliers], center, destination=True)):
        return None
    for alternative_count, _, alternative, _ in models[1:]:
        distinct = np.linalg.norm(alternative - center) > 2 * max(_tolerance(center), _tolerance(alternative))
        if distinct and alternative_count >= max(8, count * (0.25 if stationary else 0.40)):
            return None
    return float(center[0]), float(center[1])
