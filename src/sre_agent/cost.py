"""Local cost model (specs/011-efficiency-capacity-cost.md).

There is no cloud bill locally, so cost is a deterministic **cost-units** proxy derived from
resource *requests* (what you reserve/pay for), optionally dollarized with a configurable price
table. Cost-units let us compare before/after and quantify right-sizing savings honestly.
"""

from __future__ import annotations


def parse_cpu_millicores(v: str | int | float | None) -> int:
    """'500m' -> 500, '1' -> 1000, 0.5 -> 500. None/'' -> 0."""
    if v is None or v == "":
        return 0
    s = str(v).strip()
    if s.endswith("m"):
        return int(float(s[:-1]))
    return int(float(s) * 1000)


def parse_mem_mib(v: str | int | None) -> int:
    """'512Mi' -> 512, '1Gi' -> 1024, '134217728' (bytes) -> 128. None/'' -> 0."""
    if v is None or v == "":
        return 0
    s = str(v).strip()
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
             "K": 1000 / (1024 * 1024), "M": 1e6 / (1024 * 1024), "G": 1e9 / (1024 * 1024)}
    for suf, mul in units.items():
        if s.endswith(suf):
            return int(float(s[: -len(suf)]) * mul)
    # bare number = bytes
    return int(float(s) / (1024 * 1024))


def cost_units(cpu_m: int, mem_mi: int, replicas: int, mem_weight: float = 0.5) -> float:
    """Relative reserved-resource cost: replicas x (cpu_millicores + mem_MiB x weight)."""
    return round(replicas * (cpu_m + mem_mi * mem_weight), 1)


def dollars_per_month(cpu_m: int, mem_mi: int, replicas: int,
                      price_vcpu_hour: float, price_gib_hour: float) -> float | None:
    """Optional dollarization. Returns None if pricing is disabled (both prices 0)."""
    if price_vcpu_hour <= 0 and price_gib_hour <= 0:
        return None
    hours = 730.0  # avg hours/month
    vcpu = (cpu_m / 1000.0) * replicas
    gib = (mem_mi / 1024.0) * replicas
    return round((vcpu * price_vcpu_hour + gib * price_gib_hour) * hours, 2)


def savings_str(before: float, after: float, unit: str = "cost-units") -> str:
    delta = before - after
    pct = (delta / before * 100.0) if before else 0.0
    sign = "-" if delta >= 0 else "+"
    return f"{before:.1f} -> {after:.1f} {unit} ({sign}{abs(pct):.0f}%)"
