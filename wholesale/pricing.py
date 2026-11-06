"""
What a trade customer pays (G39), per unit ex VAT (per kg for weighed products), for a product at a quantity on a
UK date. Levels, in order: price lists assigned to the customer, then lists assigned to its group, then the
product's ``wholesale_price``. Never the till price: with none of these the product has no trade price.

At a level, a list counts when it is active, valid on the date and has a break the quantity reaches; the highest
``priority`` wins, then the newest (highest id). In that list the largest break reached prices the whole line (no
tiers). Prices stay at 4 dp: line totals and VAT are worked out later (G40), by the money rules.

``resolve_prices`` runs a fixed number of queries whatever the number of lines (two for the lists, one for their
entries), so a 200-line order is priced at once.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q

from vat.services import uk_date

from .models import KG, PriceList, PriceListEntry, min_quantity_problem

CUSTOMER_LIST, GROUP_LIST, BASE = 'customer_list', 'group_list', 'base'
SOURCES = [CUSTOMER_LIST, GROUP_LIST, BASE]


@dataclass(frozen=True)
class ResolvedPrice:
    unit_price: Decimal
    source: str
    price_list: PriceList | None = None
    min_quantity: Decimal | None = None


class NoPrice(Exception):
    """No list prices the product for this customer and it has no wholesale price."""


def _quantity(product, quantity):
    if isinstance(quantity, bool) or not isinstance(quantity, (int, Decimal)):
        raise ValueError('A quantity is an int or a Decimal.')
    quantity = Decimal(quantity)
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError('A quantity is above 0.')
    if product.is_weighed and quantity != quantity.quantize(KG):
        raise ValueError('Kg to 3 decimal places.')
    problem = min_quantity_problem(product, quantity)
    if problem:
        raise ValueError(problem)
    return quantity


def _lists(company_id, on, **assigned):
    valid = (Q(valid_from__isnull=True) | Q(valid_from__lte=on)) & (Q(valid_to__isnull=True) | Q(valid_to__gte=on))
    return {
        price_list.pk: price_list
        for price_list in PriceList.objects.filter(valid, company_id=company_id, is_active=True, **assigned)
        .only('id', 'name', 'priority').distinct()
    }


def resolve_prices(customer, lines, on=None):
    """
    ``lines`` is ``[(product, quantity)]``; returns one ``ResolvedPrice`` per line, or ``None`` where the product
    has no trade price. Raises ``ValueError`` for a product of another company or a bad quantity (an int or a
    Decimal above 0: whole units, or kg to 3 dp).
    """
    lines = [(product, quantity) for product, quantity in lines]
    for product, _quantity_ in lines:
        if product.company_id != customer.company_id:
            raise ValueError("A product of another company can't be priced for this customer.")
    lines = [(product, _quantity(product, quantity)) for product, quantity in lines]
    if not lines:
        return []
    on = on or uk_date()
    levels = [(CUSTOMER_LIST, _lists(customer.company_id, on, customers=customer))]
    if customer.group_id is not None:
        levels.append((GROUP_LIST, _lists(customer.company_id, on, groups=customer.group_id)))
    all_lists = {pk: price_list for _source, lists in levels for pk, price_list in lists.items()}

    # product id -> list id -> [(min quantity, unit price)]
    breaks = {}
    if all_lists:
        entries = PriceListEntry.objects.filter(
            price_list_id__in=all_lists, product_id__in={product.pk for product, _q in lines},
        ).values_list('product_id', 'price_list_id', 'min_quantity', 'unit_price')
        for product_id, list_id, min_quantity, unit_price in entries:
            breaks.setdefault(product_id, {}).setdefault(list_id, []).append((min_quantity, unit_price))

    results = []
    for product, quantity in lines:
        results.append(_resolve_line(product, quantity, levels, breaks.get(product.pk, {})))
    return results


def _resolve_line(product, quantity, levels, product_breaks):
    for source, lists in levels:
        best = None  # (priority, list id, min quantity, unit price)
        for list_id, list_breaks in product_breaks.items():
            if list_id not in lists:
                continue
            reached = [b for b in list_breaks if b[0] <= quantity]
            if not reached:
                continue
            min_quantity, unit_price = max(reached)
            candidate = (lists[list_id].priority, list_id, min_quantity, unit_price)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is not None:
            return ResolvedPrice(best[3], source, lists[best[1]], best[2])
    if product.wholesale_price is not None:
        return ResolvedPrice(product.wholesale_price, BASE)
    return None


def resolve_price(customer, product, quantity, on=None):
    """The one-line form of ``resolve_prices``; raises ``NoPrice`` when the product has no trade price."""
    [result] = resolve_prices(customer, [(product, quantity)], on=on)
    if result is None:
        raise NoPrice(product.pk)
    return result
