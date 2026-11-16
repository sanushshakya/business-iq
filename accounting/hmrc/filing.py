"""
Filing the VAT return with HMRC (G56): obligations, submit, and checking an attempt that got no clear answer.

The order matters. Under the company lock the period's postings are made, the boxes worked out and an
``HmrcSubmission`` saved as ``sending`` (committed before HMRC is called, and only one open attempt per period). Only
HMRC's 201, or HMRC's own copy with the same boxes (``check``), files the return: G54's ``record_return`` stores it
with the boxes as sent, posts the settlement and locks the months. A refusal locks nothing; no answer, a 5xx or
``DUPLICATE_SUBMISSION`` leave it ``unknown``, and it is never sent again blindly.
"""

from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from accounting.models import HmrcConnection, HmrcSubmission, VatReturn
from accounting.vat_return import VatReturnError, check_period, prepare, record_return
from tenants.models import Company
from vat.services import uk_date

from . import client
from .oauth import vrn_of

BOXES = ['box1', 'box2', 'box3', 'box4', 'box5', 'box6', 'box7', 'box8', 'box9']
HMRC_FIELDS = {'box1': 'vatDueSales', 'box2': 'vatDueAcquisitions', 'box3': 'totalVatDue',
               'box4': 'vatReclaimedCurrPeriod', 'box5': 'netVatDue', 'box6': 'totalValueSalesExVAT',
               'box7': 'totalValuePurchasesExVAT', 'box8': 'totalValueGoodsSuppliedExVAT',
               'box9': 'totalAcquisitionsExVAT'}
WHOLE_POUNDS = {'box6', 'box7', 'box8', 'box9'}
UNKNOWN_CODES = {'DUPLICATE_SUBMISSION'}


class FilingError(ValueError):
    """Refused before anything was sent (``conflict``: another attempt is open for the period)."""

    def __init__(self, detail, conflict=False):
        super().__init__(detail)
        self.detail, self.conflict = detail, conflict


def connection_for(company):
    connection = HmrcConnection.objects.filter(company=company).first()
    if connection is None:
        raise FilingError('Connect to HMRC first.')
    if connection.vrn != vrn_of(company):
        raise FilingError("The company's VAT number changed since it was connected. Connect to HMRC again.")
    return connection


def _vrn_path(connection, rest=''):
    return f'/organisations/vat/{connection.vrn}{rest}'


def obligations(connection, fraud, status=None):
    """HMRC's VAT periods for the last 366 days, each with ``filed_in_iq``."""
    today = uk_date()
    params = {'from': (today - timedelta(days=365)).isoformat(), 'to': today.isoformat()}
    if status:
        params['status'] = status
    rows = client.call(connection, 'GET', _vrn_path(connection, '/obligations'), fraud=fraud,
                       params=params).json().get('obligations', [])
    filed = set(VatReturn.objects.filter(company=connection.company).values_list('period_start', 'period_end'))
    for row in rows:
        row['filed_in_iq'] = (date.fromisoformat(row['start']), date.fromisoformat(row['end'])) in filed
    return rows


def body(submission):
    """The return as HMRC's ``Submit VAT return for period`` takes it."""
    sent = {'periodKey': submission.period_key}
    for box, field in HMRC_FIELDS.items():
        value = getattr(submission, box)
        sent[field] = int(value) if box in WHOLE_POUNDS else float(value)
    sent['finalised'] = True
    return sent


def _is_open_obligation(connection, fraud, period_key, start, end):
    return any(row.get('periodKey') == period_key and row.get('start') == start.isoformat()
               and row.get('end') == end.isoformat() for row in obligations(connection, fraud, status='O'))


def submit(company, user, period_key, start, end, fraud):
    """Submit the return for an open obligation; returns the ``HmrcSubmission`` (accepted, rejected or unknown)."""
    connection = connection_for(company)
    pending = HmrcSubmission.objects.filter(company=company, period_key=period_key,
                                            status=HmrcSubmission.UNKNOWN).first()
    if pending is not None:
        check(pending, user, fraud)
        if pending.status != HmrcSubmission.REJECTED:
            raise FilingError("An earlier submission for this period has no clear answer yet. Check it with HMRC "
                              "before submitting again.", conflict=True)
    try:
        check_period(start, end)
    except VatReturnError as error:
        raise FilingError(error.detail) from None
    if not _is_open_obligation(connection, fraud, period_key, start, end):
        raise FilingError('This is not an open HMRC period: choose one from the list of obligations.')
    try:
        with transaction.atomic():
            boxes = prepare(company, start, end, user)
            if HmrcSubmission.objects.filter(company=company, period_key=period_key,
                                             status__in=[HmrcSubmission.SENDING, HmrcSubmission.ACCEPTED]).exists():
                raise FilingError('This period is already being submitted.', conflict=True)
            submission = HmrcSubmission.objects.create(
                company=company, period_key=period_key, period_start=start, period_end=end, submitted_by=user,
                net_direction=boxes['net_direction'], **{box: boxes[box] for box in BOXES})
    except VatReturnError as error:
        raise FilingError(error.detail) from None
    except IntegrityError:
        raise FilingError('This period is already being submitted.', conflict=True) from None
    try:
        answer = client.call(connection, 'POST', _vrn_path(connection, '/returns'), fraud=fraud,
                             json=body(submission)).json()
    except client.HmrcError as error:
        unknown = error.status is None or error.status >= 500 or error.code in UNKNOWN_CODES
        _close(submission, HmrcSubmission.UNKNOWN if unknown else HmrcSubmission.REJECTED, error.code, error.message)
        return submission
    _accept(submission, answer, user)
    return submission


def _close(submission, status, code='', message=''):
    submission.status, submission.error_code, submission.error_message = status, code[:60], message[:500]
    submission.save(update_fields=['status', 'error_code', 'error_message'])


def _accept(submission, receipt, user):
    """File the return HMRC holds: G54's record (boxes as sent), settlement and lock, then the receipt."""
    with transaction.atomic():
        Company.objects.select_for_update().filter(pk=submission.company_id).first()
        current = HmrcSubmission.objects.select_for_update().get(pk=submission.pk)
        if current.status == HmrcSubmission.ACCEPTED:
            submission.refresh_from_db()
            return
        boxes = {box: getattr(current, box) for box in BOXES}
        boxes['net_direction'] = current.net_direction
        filed = record_return(current.company, current.period_start, current.period_end, boxes, user,
                              period_key=current.period_key)
        current.status, current.vat_return = HmrcSubmission.ACCEPTED, filed
        current.processing_date = parse_datetime(receipt.get('processingDate') or '') or None
        current.form_bundle_number = str(receipt.get('formBundleNumber') or '')[:40]
        current.payment_indicator = str(receipt.get('paymentIndicator') or '')[:10]
        current.charge_ref_number = str(receipt.get('chargeRefNumber') or '')[:40]
        current.error_code = current.error_message = ''
        current.save()
    submission.refresh_from_db()


def _same(submission, held):
    for box, field in HMRC_FIELDS.items():
        try:
            if Decimal(str(held.get(field))) != getattr(submission, box):
                return False
        except (ArithmeticError, ValueError):
            return False
    return True


def check(submission, user, fraud):
    """Settle an ``unknown`` attempt from the return HMRC holds for its period: the same boxes → filed; none →
    rejected (``NOT_RECEIVED``, a new attempt may be made); other figures → still unknown (a person must look)."""
    if submission.status != HmrcSubmission.UNKNOWN:
        return submission
    connection = connection_for(submission.company)
    path = _vrn_path(connection, f'/returns/{quote(submission.period_key, safe="")}')
    try:
        held = client.call(connection, 'GET', path, fraud=fraud).json()
    except client.HmrcError as error:
        if error.status == 404:
            _close(submission, HmrcSubmission.REJECTED, 'NOT_RECEIVED', 'HMRC has no return for this period.')
        return submission
    if not _same(submission, held):
        _close(submission, HmrcSubmission.UNKNOWN, 'FIGURES_DIFFER',
               'HMRC holds a return for this period with other figures. Compare it with IQ before doing anything.')
        return submission
    _accept(submission, held, user)
    return submission
