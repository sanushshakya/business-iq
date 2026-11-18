"""
Filing the VAT return with HMRC (G56): obligations, submit, and checking an attempt that got no clear answer.

The order matters. Under the company lock the period's postings are made, the boxes worked out and an
``HmrcSubmission`` saved as ``sending`` (committed before HMRC is called, and only one open attempt per period). Only
HMRC's 201, or HMRC's own copy with the same boxes (``check``), files the return: G54's ``record_return`` stores it
with the boxes as sent, posts the settlement and locks the months. A refusal locks nothing; no answer, a 5xx or
``DUPLICATE_SUBMISSION`` leave it ``unknown``, and it is never sent again blindly.

Every attempt has a way out: a ``sending`` row older than ``STALE_AFTER`` (its process died) is treated as unknown;
``check`` reads HMRC's copy; if HMRC holds it but IQ can't record it, it stays unknown (``NOT_RECORDED``) and can be
checked again; and staff ``resolve`` an attempt HMRC holds with other figures (record HMRC's copy, or close it). While
an attempt may be at HMRC, ``journal`` refuses new postings in the period and locks over it, so the boxes sent stay
the period's and recording can't fail on a lock.
"""

from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounting.journal import JournalError
from accounting.models import HmrcConnection, HmrcSubmission, VatReturn
from accounting.vat_return import VatReturnError, check_period, prepare, record_return, undeclared, vat_boxes
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
RECORDING_ERRORS = (VatReturnError, JournalError, ValidationError, IntegrityError)


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
        sent[field] = int(value) if box in WHOLE_POUNDS else float(value) + 0.0  # + 0.0: never -0.0
    sent['finalised'] = True
    return sent


def _is_open_obligation(connection, fraud, period_key, start, end):
    return any(row.get('periodKey') == period_key and row.get('start') == start.isoformat()
               and row.get('end') == end.isoformat() for row in obligations(connection, fraud, status='O'))


def submit(company, user, period_key, start, end, fraud):
    """Submit the return for an open obligation; returns the ``HmrcSubmission`` (accepted, rejected or unknown)."""
    connection = connection_for(company)
    _expire_stale(company)
    pending = HmrcSubmission.objects.filter(company=company, period_key=period_key,
                                            status=HmrcSubmission.UNKNOWN).first()
    if pending is not None:
        try:
            check(pending, user, fraud)
        except client.HmrcError:
            pass  # HMRC can't say yet: it stays unknown, refused below
        if pending.status == HmrcSubmission.ACCEPTED:
            raise FilingError('This period is already filed: an earlier attempt reached HMRC and is now recorded.',
                              conflict=True)
        if pending.status != HmrcSubmission.REJECTED:
            raise FilingError("An earlier submission for this period has no clear answer yet. Check it with HMRC "
                              "before submitting again.", conflict=True)
    try:
        check_period(start, end)
    except VatReturnError as error:
        raise FilingError(error.detail) from None
    if not _is_open_obligation(connection, fraud, period_key, start, end):
        raise FilingError('This is not an open HMRC period: choose one from the list of obligations.')
    client.access_token(connection)  # refresh now, so a refusal or no answer here sends and records nothing
    try:
        with transaction.atomic():
            Company.objects.select_for_update().filter(pk=company.pk).first()
            _refuse_while_earlier_waits(company, period_key, end)
            boxes = prepare(company, start, end, user)
            if HmrcSubmission.objects.filter(company=company, period_key=period_key,
                                             status__in=[HmrcSubmission.SENDING, HmrcSubmission.ACCEPTED]).exists():
                raise FilingError('This period is already being submitted.', conflict=True)
            submission = HmrcSubmission.objects.create(
                company=company, period_key=period_key, period_start=start, period_end=end, submitted_by=user,
                vrn=connection.vrn,
                net_direction=boxes['net_direction'], **{box: boxes[box] for box in BOXES})
    except VatReturnError as error:
        raise FilingError(error.detail) from None
    except IntegrityError:
        raise FilingError('This period is already being submitted.', conflict=True) from None
    try:
        answer = client.json_of(client.call(connection, 'POST', _vrn_path(connection, '/returns'), fraud=fraud,
                                            json=body(submission)))
    except client.HmrcError as error:
        unknown = error.status is None or error.status >= 500 or error.code in UNKNOWN_CODES
        _close(submission, HmrcSubmission.UNKNOWN if unknown else HmrcSubmission.REJECTED, error.code, error.message)
        return submission
    _accept(submission, answer, user)
    return submission


def _refuse_while_earlier_waits(company, period_key, end):
    """Another period's attempt still waiting for HMRC, starting on or before this one ends, would stop this return's
    months being locked once HMRC accepted it (``journal.lock_period``): refuse before anything is sent."""
    waiting = (HmrcSubmission.objects.filter(company=company, status__in=HmrcSubmission.PENDING, period_start__lte=end)
               .exclude(period_key=period_key).order_by('period_start').first())
    if waiting is not None:
        raise FilingError(f'The VAT return for period {waiting.period_key} ({waiting.period_start:%d/%m/%Y} to '
                          f"{waiting.period_end:%d/%m/%Y}) has no answer from HMRC yet. Check it with HMRC, or close "
                          'it, before submitting this one.', conflict=True)


def _close(submission, status, code='', message=''):
    submission.status, submission.error_code, submission.error_message = status, code[:60], message[:500]
    submission.save(update_fields=['status', 'error_code', 'error_message'])


def _expire_stale(company):
    """``sending`` rows whose process died (older than ``STALE_AFTER``) become unknown, to be checked."""
    HmrcSubmission.objects.filter(company=company, status=HmrcSubmission.SENDING,
                                  submitted_at__lt=timezone.now() - HmrcSubmission.STALE_AFTER).update(
        status=HmrcSubmission.UNKNOWN, error_code='INTERRUPTED',
        error_message='The submission was interrupted before HMRC answered.')


def _accept(submission, receipt, user, boxes=None, code='', message=''):
    """File the return HMRC holds: G54's record (the boxes as sent, or ``boxes``), settlement and lock, then the
    receipt. If IQ can't record it, the attempt stays unknown (``NOT_RECORDED``) so it can be checked again."""
    try:
        _record(submission, receipt, user, boxes, code, message)
    except RECORDING_ERRORS as error:
        detail = getattr(error, 'detail', None) or '; '.join(getattr(error, 'messages', [])) or type(error).__name__
        _close(submission, HmrcSubmission.UNKNOWN, 'NOT_RECORDED',
               f'HMRC has this return but IQ could not record it ({detail}). Check with HMRC again once fixed.')
    submission.refresh_from_db()


def _record(submission, receipt, user, boxes, code, message):
    with transaction.atomic():
        Company.objects.select_for_update().filter(pk=submission.company_id).first()
        current = HmrcSubmission.objects.select_for_update().get(pk=submission.pk)
        if current.status == HmrcSubmission.ACCEPTED:
            return
        if boxes is None:
            boxes = {box: getattr(current, box) for box in BOXES}
            boxes['net_direction'] = current.net_direction
        current.status = HmrcSubmission.ACCEPTED  # first, so the journal guard lets its settlement and lock through
        current.save(update_fields=['status'])
        filed = record_return(current.company, current.period_start, current.period_end, boxes, user,
                              period_key=current.period_key)
        current.vat_return = filed
        current.processing_date = parse_datetime(receipt.get('processingDate') or '') or None
        current.form_bundle_number = str(receipt.get('formBundleNumber') or '')[:40]
        current.payment_indicator = str(receipt.get('paymentIndicator') or '')[:10]
        current.charge_ref_number = str(receipt.get('chargeRefNumber') or '')[:40]
        current.error_code, current.error_message = code, message[:500]
        current.save()


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
    _expire_stale(submission.company)
    submission.refresh_from_db()
    if submission.status != HmrcSubmission.UNKNOWN:
        return submission
    try:
        held = _held(submission, fraud)
    except client.HmrcError as error:
        if error.status != 404:
            raise
        _close(submission, HmrcSubmission.REJECTED, 'NOT_RECEIVED', 'HMRC has no return for this period.')
        return submission
    if not _same(submission, held):
        _close(submission, HmrcSubmission.UNKNOWN, 'FIGURES_DIFFER',
               'HMRC holds a return for this period with other figures. Compare it with IQ before doing anything.')
        return submission
    _accept(submission, held, user)
    return submission


def _held(submission, fraud):
    """The return HMRC holds for the attempt's period, read from the VRN it was sent to."""
    connection = connection_for(submission.company)
    if submission.vrn and submission.vrn != connection.vrn:
        raise FilingError(f'This attempt went to VAT number {submission.vrn}; the company is now connected with '
                          'another VAT number. Reconnect with that one to check it.')
    path = _vrn_path(connection, f'/returns/{quote(submission.period_key, safe="")}')
    return client.json_of(client.call(connection, 'GET', path, fraud=fraud))


def resolve(submission, user, fraud, action, reason):
    """Staff settle an attempt that is still unknown: ``close`` it (HMRC doesn't hold it, or it is handled outside
    IQ), or ``record_hmrc``: record the return HMRC holds for the period, with HMRC's figures, and lock its months."""
    reason = (reason or '').strip()
    if not reason or len(reason) > 300:
        raise FilingError('Give a reason (at most 300 characters).')
    _expire_stale(submission.company)
    submission.refresh_from_db()
    if submission.status != HmrcSubmission.UNKNOWN:
        raise FilingError('Only an attempt with no clear answer can be resolved.')
    if action == 'close':
        _close(submission, HmrcSubmission.REJECTED, 'CLOSED', f'Closed by staff: {reason}')
        return submission
    held = _held(submission, fraud)
    boxes = {}
    for box, field in HMRC_FIELDS.items():
        value = Decimal(str(held.get(field, 0)))
        boxes[box] = value.quantize(Decimal('1') if box in WHOLE_POUNDS else Decimal('0.01'))
    boxes['net_direction'] = VatReturn.PAY if boxes['box3'] >= boxes['box4'] else VatReturn.RECLAIM
    message = f"Recorded from HMRC's copy: {reason}"
    left = undeclared(vat_boxes(submission.company, submission.period_start, submission.period_end), boxes)
    if left:
        message += f" IQ's ledger differs by £{left}: the difference is in Suspense, to correct."
    _accept(submission, held, user, boxes=boxes, code='RECORDED_FROM_HMRC', message=message)
    if submission.status != HmrcSubmission.ACCEPTED:
        raise FilingError(submission.error_message)
    return submission
