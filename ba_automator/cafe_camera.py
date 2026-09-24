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


def _affine_motion(source: np.ndarray, destination: np.ndarray, models: list,
                   diagnostics: dict) -> tuple[float, float] | None:
    """Recover a coherent pan whose small perspective change defeats translation.

    Affine consensus can establish movement only. It cannot establish that the
    camera stopped, so a weak/near-zero result remains unknown.
    """
    matrix, mask = cv2.estimateAffine2D(source, destination, method=cv2.RANSAC,
                                      ransacReprojThreshold=4.0, maxIters=512,
                                      confidence=.995, refineIters=10)
    if matrix is None or mask is None or not np.isfinite(matrix).all():
        diagnostics["reason"] = "affine_fit_failed"
        return None
    inliers = mask.ravel().astype(bool)
    count = int(inliers.sum())
    diagnostics.update(affine_inliers=count, affine_ratio=round(count / len(source), 3))
    if count < 24 or count / len(source) < .65:
        diagnostics["reason"] = "affine_consensus_too_weak"
        return None
    linear = matrix[:, :2]
    scales = np.linalg.svd(linear, compute_uv=False)
    # Permit mild perspective/shear from the game's camera, not an arbitrary
    # transform that could join unrelated furniture or a changing screen.
    if (np.linalg.det(linear) <= 0 or scales.min() < .85 or scales.max() > 1.15
            or scales.max() / scales.min() > 1.25
            or np.max(np.abs(linear - np.eye(2))) > .20):
        diagnostics["reason"] = "affine_geometry_not_camera_pan"
        return None
    deltas = destination - source
    center = np.median(deltas[inliers], axis=0)
    magnitude = float(np.linalg.norm(center))
    if magnitude <= 5:
        diagnostics["reason"] = "affine_cannot_certify_stationary"
        return None
    forward = deltas[inliers] @ (center / magnitude)
    if np.mean(forward > max(3.0, magnitude * .4)) < .90:
        diagnostics["reason"] = "affine_motion_has_conflicting_directions"
        return None
    if (not _spread(source[inliers], center)
            or not _spread(destination[inliers], center, destination=True)):
        diagnostics["reason"] = "affine_support_too_localized"
        return None
    for _, _, alternative, alternative_inliers in models:
        distinct = np.linalg.norm(alternative - center) > 2 * max(_tolerance(center), _tolerance(alternative))
        unexplained = int((alternative_inliers & ~inliers).sum())
        if distinct and unexplained >= max(8, count * .35):
            diagnostics["reason"] = "affine_has_competing_motion"
            return None
    diagnostics.update(method="affine", reason="accepted", inliers=count,
                       ratio=round(count / len(source), 3),
                       displacement=[round(float(value), 3) for value in center])
    return float(center[0]), float(center[1])


def measure_camera_displacement(
    before: bytes | np.ndarray,
    after: bytes | np.ndarray,
    *,
    diagnostics: dict | None = None,
) -> tuple[float, float] | None:
    """Return scene (dx, dy), or None when camera movement is not established.

    Inputs are PNG bytes or uint8 grayscale/BGR/BGRA 1280×720 frames. HUD pixels
    outside SCENE_BOUNDS never participate. Sparse, localized, unrelated, and
    competing motion estimates fail closed. In particular, None is not (0, 0).

    Work is bounded to 1,800 ORB descriptors, 13 translation hypotheses, and a
    512-iteration affine RANSAC fallback for moving scenes with perspective change.
    The optional diagnostics dict receives compact counts/rejection reasons; it
    never contains images or OCR. No global OpenCV RNG/thread settings are changed.
    """
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.clear()
    diagnostics.update(method=None, reason="invalid_frame")
    first, second = _scene_gray(before), _scene_gray(after)
    if first is None or second is None:
        return None
    detector = cv2.ORB_create(nfeatures=MAX_FEATURES, scaleFactor=1.2, nlevels=8,
                             edgeThreshold=19, fastThreshold=12)
    first_points, first_descriptors = detector.detectAndCompute(first, None)
    second_points, second_descriptors = detector.detectAndCompute(second, None)
    diagnostics["features"] = [len(first_points), len(second_points)]
    if (first_descriptors is None or second_descriptors is None
            or len(first_points) < MIN_MATCHES or len(second_points) < MIN_MATCHES):
        diagnostics["reason"] = "too_few_features"
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    forward = _distinct_matches(matcher.knnMatch(first_descriptors, second_descriptors, k=2))
    backward = _distinct_matches(matcher.knnMatch(second_descriptors, first_descriptors, k=2))
    matches = [match for match in forward.values()
               if match.trainIdx in backward and backward[match.trainIdx].trainIdx == match.queryIdx]
    diagnostics["matches"] = len(matches)
    if len(matches) < MIN_MATCHES:
        diagnostics["reason"] = "too_few_distinct_matches"
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
        diagnostics["reason"] = "no_translation_consensus"
        return _affine_motion(source, destination, models, diagnostics)
    models.sort(key=lambda model: (-model[0], model[1]))
    count, _, center, inliers = models[0]
    diagnostics.update(translation_inliers=count, translation_ratio=round(count / len(matches), 3))
    stationary = float(np.linalg.norm(center)) <= 4.0
    rejection = None
    if count < MIN_MATCHES or count / len(matches) < (0.75 if stationary else 0.60):
        rejection = "translation_consensus_too_weak"
    elif (not _spread(source[inliers], center)
            or not _spread(destination[inliers], center, destination=True)):
        rejection = "translation_support_too_localized"
    else:
        for alternative_count, _, alternative, _ in models[1:]:
            distinct = np.linalg.norm(alternative - center) > 2 * max(_tolerance(center), _tolerance(alternative))
            if distinct and alternative_count >= max(8, count * (0.25 if stationary else 0.40)):
                rejection = "competing_translation_motions"
                break
    if rejection:
        diagnostics.update(reason=rejection, translation_rejection=rejection)
        if stationary:
            return None
        return _affine_motion(source, destination, models, diagnostics)
    diagnostics.update(method="translation", reason="accepted", inliers=count,
                       ratio=round(count / len(matches), 3),
                       displacement=[round(float(value), 3) for value in center])
    return float(center[0]), float(center[1])
