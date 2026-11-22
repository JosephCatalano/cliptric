"""Bounded crop commands, mapped through the same retained intervals as media."""

from bisect import bisect_right

from .follow_models import FollowPlan


def follow_position(plan: FollowPlan, at: float) -> tuple[float, float]:
    index = bisect_right(plan.points, at, key=lambda point: point.at) - 1
    first = plan.points[max(0, index)]
    last = plan.points[min(len(plan.points) - 1, max(0, index + 1))]
    fraction = (
        max(0.0, min(1.0, (at - first.at) / (last.at - first.at))) if last.at > first.at else 0.0
    )
    return first.x + (last.x - first.x) * fraction, first.y + (last.y - first.y) * fraction


def follow_commands(
    plan: FollowPlan, kept: list[tuple[float, float]], width: int, height: int
) -> str:
    """Keep FFmpeg expressions shallow even for hundreds of tracking observations."""
    crop_w = max(2, int(plan.width * width) // 2 * 2)
    crop_h = max(2, int(plan.height * height) // 2 * 2)
    commands = []
    offset = 0.0
    for start, end in kept:
        samples = [start, *(p.at for p in plan.points if start < p.at < end), end]
        for first, last in zip(samples, samples[1:]):
            a, b = follow_position(plan, first), follow_position(plan, last)
            at = offset + first - start
            expressions = []
            for axis, scale, maximum, i in (
                ("x", width, width - crop_w, 0),
                ("y", height, height - crop_h, 1),
            ):
                x, y = min(maximum, max(0, a[i] * scale)), min(maximum, max(0, b[i] * scale))
                expressions.append(
                    f"crop@follow {axis} '{x:.8f}+({y - x:.8f})*(t-{at:.8f})/{last - first:.8f}'"
                )
            commands.append(f"{at:.8f} " + ", ".join(expressions) + ";")
        offset += end - start
    x, y = follow_position(plan, kept[-1][1])
    commands.append(
        f"{offset:.8f} crop@follow x '{min(width - crop_w, x * width):.8f}', crop@follow y '{min(height - crop_h, y * height):.8f}';"
    )
    return "\n".join(commands) + "\n"
