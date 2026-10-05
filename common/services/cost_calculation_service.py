# common/services/cost_calculation_service.py

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.conf import settings

from .hmrctariff_service import HMRCTariffService, TariffLookupError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LandedCost:
    goods_cost: Decimal
    duty_rate: Decimal        # as a fraction, e.g. 0.06 for 6%
    duty_amount: Decimal
    total: Decimal
    duty_source: str          # 'tariff', 'tariff_preference' or 'default' (see ``note``)
    note: str = ''


class CostCalculationService:
    """
    Landed cost of a product: its price plus import duty.

    Duty comes from the UK Trade Tariff via ``HMRCTariffService`` using the product's 10 digit
    ``commodity_code``. When that is not possible (no code, service unreachable, duty that is not a plain
    percentage) the configured ``DEFAULT_CUSTOMS_DUTY_RATE`` is used and the result says so, because that
    is only an estimate.
    """

    def __init__(self, tariff_service: Optional[HMRCTariffService] = None):
        self.tariff_service = tariff_service or HMRCTariffService()

    def _default_rate(self) -> Decimal:
        return Decimal(str(settings.DEFAULT_CUSTOMS_DUTY_RATE))

    def get_customs_duty_rate(self, commodity_code: str, origin: Optional[str] = None) -> Decimal:
        """Duty as a fraction (0.06 = 6%), falling back to the default rate."""
        return self._duty_rate(commodity_code, origin)[0]

    def _duty_rate(self, commodity_code, origin):
        """Return ``(rate, source, note)``."""
        if not commodity_code:
            return self._default_rate(), 'default', 'Product has no commodity code.'
        try:
            duty = self.tariff_service.get_duty(commodity_code, origin=origin)
        except (TariffLookupError, ValueError) as exc:
            logger.warning("Using the default duty rate for %s: %s", commodity_code, exc)
            return self._default_rate(), 'default', str(exc)
        if duty.rate_percent is None:
            note = f"Duty '{duty.expression}' is not a plain percentage."
            logger.warning("Using the default duty rate for %s: %s", commodity_code, note)
            return self._default_rate(), 'default', note
        source = 'tariff_preference' if duty.source == 'preference' else 'tariff'
        return duty.rate_percent / Decimal(100), source, ''

    def landed_cost_breakdown(self, product, quantity, origin: Optional[str] = None) -> LandedCost:
        goods_cost = product.price * quantity
        rate, source, note = self._duty_rate(product.commodity_code, origin)
        duty_amount = (goods_cost * rate).quantize(Decimal('0.01'))
        return LandedCost(goods_cost, rate, duty_amount, goods_cost + duty_amount, source, note)

    def calculate_landed_cost(self, product, quantity, origin: Optional[str] = None) -> Decimal:
        """Total cost (goods plus duty) for ``quantity`` units."""
        return self.landed_cost_breakdown(product, quantity, origin).total
