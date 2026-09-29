"""Market price grids.

Each Kalshi market publishes ``price_ranges``: bands of ``{start, end, step}``
in dollars. Tick sizes differ per market (1c, 0.1c, and finer), so every price
the bot sends is snapped to the market's own grid.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from kalshi_lp.core.types import ONE, ZERO, to_decimal

CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class PriceBand:
    start: Decimal
    end: Decimal
    step: Decimal

    def contains(self, price: Decimal) -> bool:
        return self.start <= price <= self.end


class PriceGrid:
    """The set of valid order prices for a market, strictly between 0 and 1."""

    def __init__(self, bands: Iterable[PriceBand]):
        self.bands = tuple(sorted(bands, key=lambda b: b.start))
        if not self.bands:
            raise ValueError("price grid needs at least one band")

    @classmethod
    def linear_cent(cls) -> PriceGrid:
        return cls([PriceBand(ZERO, ONE, CENT)])

    @classmethod
    def from_api(cls, price_ranges: Iterable[Mapping[str, Any]] | None) -> PriceGrid:
        bands = [
            PriceBand(to_decimal(r["start"]), to_decimal(r["end"]), to_decimal(r["step"]))
            for r in price_ranges or []
            if to_decimal(r.get("step")) > 0
        ]
        return cls(bands) if bands else cls.linear_cent()

    @property
    def min_price(self) -> Decimal:
        return self.round_up(self.bands[0].start + self.bands[0].step)

    @property
    def max_price(self) -> Decimal:
        return self.round_down(self.bands[-1].end - self.bands[-1].step)

    def mirrored(self) -> PriceGrid:
        """The grid in NO-price terms (``p -> 1 - p``)."""
        return PriceGrid(PriceBand(ONE - b.end, ONE - b.start, b.step) for b in self.bands)

    def _band_for(self, price: Decimal, *, upward: bool = False) -> PriceBand:
        """Band containing ``price``. At a shared boundary, ``upward`` picks the upper band."""
        candidates = reversed(self.bands) if upward else iter(self.bands)
        for band in candidates:
            if band.contains(price):
                return band
        # Outside every band: use the nearest one.
        return self.bands[0] if price < self.bands[0].start else self.bands[-1]

    def _snap(self, price: Decimal, rounding: str) -> Decimal:
        band = self._band_for(price)
        steps = ((price - band.start) / band.step).to_integral_value(rounding=rounding)
        snapped = band.start + steps * band.step
        return min(max(snapped, band.start), band.end)

    def round_down(self, price: Decimal) -> Decimal:
        return self._snap(price, ROUND_FLOOR)

    def round_up(self, price: Decimal) -> Decimal:
        return self._snap(price, ROUND_CEILING)

    def is_valid(self, price: Decimal) -> bool:
        return ZERO < price < ONE and self.round_down(price) == price

    def tick_below(self, price: Decimal) -> Decimal:
        """Largest valid price strictly below ``price``."""
        candidate = self.round_down(price)
        if candidate < price:
            return candidate
        return self.round_down(price - self._band_for(price).step)

    def tick_above(self, price: Decimal) -> Decimal:
        """Smallest valid price strictly above ``price``."""
        candidate = self.round_up(price)
        if candidate > price:
            return candidate
        return self.round_up(price + self._band_for(price, upward=True).step)

    def ticks_between(self, low: Decimal, high: Decimal) -> int:
        """Number of grid ticks from ``low`` up to ``high`` (0 if ``low >= high``)."""
        ticks = 0
        price = low
        while price < high:
            price = self.tick_above(price)
            ticks += 1
            if ticks > 100_000:  # defensive: malformed grid
                break
        return ticks
