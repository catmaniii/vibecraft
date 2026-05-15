from typing import Optional, Tuple
from math import atan2, sqrt, acos


def get_angle_between_points(
    p1: Tuple[int, int], p2: Tuple[int, int], p3: Optional[Tuple[int, int]] = None
) -> float:
    """Get the angle between two points, relative to a third if provided."""
    if not p3:
        return atan2(p2[1] - p1[1], p2[0] - p1[0])
    # move the third point to the origin and redefine vectors
    a = p1[0] - p3[0], p1[1] - p3[1]
    b = p2[0] - p3[0], p2[1] - p3[1]
    # cosine of the angle between vectors is their dot product divided by the product of their magnitudes
    mag_a = sqrt(a[0] ** 2 + a[1] ** 2)
    mag_b = sqrt(b[0] ** 2 + b[1] ** 2)
    a_dot_b = a[0] * b[0] + a[1] * b[1]

    return acos(a_dot_b / (mag_a * mag_b))
