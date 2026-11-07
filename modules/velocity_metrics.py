"""The engagement metric used by new velocity checks."""

METRIC = "reactions_comments"


def engagement_count(result: dict | None) -> int | None:
    """Both displayed totals are required; missing is different from zero."""
    if not result or result.get("outcome") != "ok":
        return None
    values = [result.get("reactions"), result.get("comments")]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)
