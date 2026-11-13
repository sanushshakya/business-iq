# promotions/engine.py

"""
Till promotions (G58): which promotions a basket gets and how much each line comes down. Pure Python, no Django; the
till runs a line-for-line port (fe ``src/utils/promotions.js``) against the same fixtures
(``promotion_fixtures.json``), so a sale prices the same online and offline.

Rules (workspace spec ``docs/superpowers/specs/2026-11-13-g58-till-promotions-design.md``):

- A line is units of one product at one price (``gross / quantity``); a weighed line is one unit priced ``gross``.
  Scanned-label lines are never discounted (the label's price wins), weighed lines only by ``category_percent``.
- Each unit is used by at most one promotion. Promotions are tried by priority, highest first; within a priority the
  one that saves most right now goes first (then the lower id), and each applies as many times as it can.
  Units are matched most expensive first (equal prices by product id, then basket order, so the answer does not
  depend on the order things were scanned), and a deal applies only when it saves money.
- ``stacks`` (``category_percent`` only) promotions apply after all the others, on what is left of each line;
  a line gets at most one (highest priority, then percent, then lower id).
- A deal's saving is spread over its units by price in whole pence (largest remainder; ties by product id, then the
  earlier unit);
  a percent is taken per line, half-up to the penny. Discounts are gross (VAT-inclusive), so the caller lowers each
  line before splitting its VAT.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MULTI_BUY, BOGOF, MEAL_DEAL, CATEGORY_PERCENT = 'multi_buy', 'bogof', 'meal_deal', 'category_percent'
CENTS = Decimal('0.01')


@dataclass(frozen=True)
class EngineLine:
    key: object
    product: int
    category: int | None
    quantity: int
    gross: Decimal  # the line before any discount
    weighed: bool = False
    label: bool = False


@dataclass(frozen=True)
class EnginePromotion:
    id: int
    name: str
    kind: str
    priority: int
    stacks: bool
    buy_quantity: int | None
    get_quantity: int | None
    deal_price: Decimal | None
    percent: Decimal | None
    products: frozenset
    categories: frozenset
    groups: tuple = ()  # meal deal: a frozenset of product ids per group


@dataclass(frozen=True)
class Applied:
    promotion: int
    name: str
    kind: str
    times: int
    saving: Decimal
    lines: tuple = ()  # keys of the lines it took something off


@dataclass
class Result:
    discounts: dict = field(default_factory=dict)  # line key -> Decimal, only lines that came down
    applied: list = field(default_factory=list)


@dataclass
class _Unit:
    line: int  # index in the basket
    order: int  # position in the basket's units
    pence: int
    product: int
    category: int | None
    weighed: bool
    used: bool = False


def _pence(amount):
    return int((amount * 100).to_integral_value())


def _units(lines):
    units = []
    for index, line in enumerate(lines):
        if line.label:
            continue
        if line.weighed:
            units.append(_Unit(index, len(units), _pence(line.gross), line.product, line.category, True))
            continue
        each = _pence(line.gross) // line.quantity if line.quantity else 0
        for _ in range(line.quantity):
            units.append(_Unit(index, len(units), each, line.product, line.category, False))
    return units


def _by_price(units):
    return sorted(units, key=lambda unit: (-unit.pence, unit.product, unit.order))


def _spread(saving, units):
    """``saving`` pence over ``units`` by price: ``[(unit, pence)]`` summing exactly to ``saving``."""
    total = sum(unit.pence for unit in units)
    shares = [(unit, saving * unit.pence // total, saving * unit.pence % total) for unit in units]
    spare = saving - sum(share for _, share, _ in shares)
    ranked = sorted(range(len(shares)), key=lambda i: (-shares[i][2], shares[i][0].product, shares[i][0].order))
    extra = set(ranked[:spare])
    return [(unit, share + (1 if i in extra else 0)) for i, (unit, share, _) in enumerate(shares)]


def _plan_sets(promotion, units):
    """Multi-buy and BOGOF: ``[(units, saving)]`` for each set that saves, most expensive units first."""
    free = [u for u in units if not u.used and not u.weighed and u.product in promotion.products]
    if promotion.kind == MULTI_BUY:
        size = promotion.buy_quantity
    else:
        size = promotion.buy_quantity + promotion.get_quantity
    sets, ranked = [], _by_price(free)
    for start in range(0, len(ranked) - size + 1, size):
        chosen = ranked[start:start + size]
        if promotion.kind == MULTI_BUY:
            saving = sum(u.pence for u in chosen) - _pence(promotion.deal_price)
        else:
            saving = sum(u.pence for u in chosen[size - promotion.get_quantity:])  # the cheapest are free
        if saving <= 0:
            break  # later sets are cheaper still
        sets.append((chosen, saving))
    return sets


def _plan_meal_deals(promotion, units):
    """One item from each group, most expensive first, while a deal saves money. A product is in one group only, so
    each group's candidates are sorted once and taken in turn."""
    queues = [_by_price([u for u in units if not u.used and not u.weighed and u.product in group])
              for group in promotion.groups]
    sets = []
    for depth in range(min((len(queue) for queue in queues), default=0)):
        chosen = [queue[depth] for queue in queues]
        saving = sum(u.pence for u in chosen) - _pence(promotion.deal_price)
        if saving <= 0:
            break  # later deals are cheaper still
        sets.append((chosen, saving))
    return sets


def _percent_of(pence, percent):
    return int((Decimal(pence) * percent / 100).quantize(Decimal('1'), ROUND_HALF_UP))


def _plan_percent(promotion, units):
    """Non-stacking % off: per line, on its units no promotion has used. ``[(units of the line, saving)]``."""
    by_line = {}
    for unit in units:
        if not unit.used and unit.category in promotion.categories:
            by_line.setdefault(unit.line, []).append(unit)
    plans = []
    for line_units in by_line.values():
        saving = _percent_of(sum(u.pence for u in line_units), promotion.percent)
        if saving > 0:
            plans.append((line_units, saving))
    return plans


def _plan(promotion, units):
    if promotion.kind == CATEGORY_PERCENT:
        return _plan_percent(promotion, units)
    if promotion.kind == MEAL_DEAL:
        return _plan_meal_deals(promotion, units)
    return _plan_sets(promotion, units)


def apply(lines, promotions):
    """The discounts for ``lines`` (``EngineLine``) under ``promotions`` (``EnginePromotion``, already the ones in
    force for this branch and day)."""
    units = _units(lines)
    off = [0] * len(lines)  # pence per line
    result = Result()
    plain = [p for p in promotions if not (p.kind == CATEGORY_PERCENT and p.stacks)]
    for priority in sorted({p.priority for p in plain}, reverse=True):
        waiting = [p for p in plain if p.priority == priority]
        while waiting:
            options = [(p, _plan(p, units)) for p in waiting]
            promotion, plans = min(options, key=lambda o: (-sum(s for _, s in o[1]), o[0].id))
            if not plans:
                break
            waiting.remove(promotion)
            for chosen, saving in plans:
                if promotion.kind == CATEGORY_PERCENT:
                    off[chosen[0].line] += saving
                else:
                    for unit, share in _spread(saving, chosen):
                        off[unit.line] += share
                for unit in chosen:
                    unit.used = True
            touched = sorted({unit.line for chosen, _ in plans for unit in chosen})
            result.applied.append(Applied(promotion.id, promotion.name, promotion.kind, len(plans),
                                          Decimal(sum(s for _, s in plans)) / 100,
                                          tuple(lines[i].key for i in touched)))

    stacking = sorted((p for p in promotions if p.kind == CATEGORY_PERCENT and p.stacks),
                      key=lambda p: (-p.priority, -p.percent, p.id))
    stacked = {}
    for index, line in enumerate(lines):
        if line.label:
            continue
        promotion = next((p for p in stacking if line.category in p.categories), None)
        if promotion is None:
            continue
        saving = _percent_of(_pence(line.gross) - off[index], promotion.percent)
        if saving > 0:
            off[index] += saving
            times, total, keys = stacked.get(promotion, (0, 0, ()))
            stacked[promotion] = (times + 1, total + saving, keys + (line.key,))
    for promotion in stacking:
        if promotion in stacked:
            times, total, keys = stacked[promotion]
            result.applied.append(Applied(promotion.id, promotion.name, promotion.kind, times, Decimal(total) / 100,
                                          keys))

    for index, line in enumerate(lines):
        if off[index]:
            result.discounts[line.key] = (Decimal(off[index]) / 100).quantize(CENTS)
    result.applied = [Applied(a.promotion, a.name, a.kind, a.times, a.saving.quantize(CENTS), a.lines)
                      for a in result.applied]
    return result
