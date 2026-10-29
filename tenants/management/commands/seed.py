import os
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import signals

from tenants.models import Company

MODEL_SIGNALS = (signals.pre_save, signals.post_save, signals.pre_delete, signals.post_delete, signals.m2m_changed)


@contextmanager
def model_signals_muted():
    """
    Load a snapshot as-is. Receivers would otherwise add rows the snapshot already holds (e.g. a
    ``StockSyncState`` for each saved product) and clash with them.
    """
    saved = [(signal, signal.receivers, signal.sender_receivers_cache.copy()) for signal in MODEL_SIGNALS]
    for signal in MODEL_SIGNALS:
        signal.receivers = []
        signal.sender_receivers_cache.clear()
    try:
        yield
    finally:
        for signal, receivers, cache in saved:
            signal.receivers = receivers
            signal.sender_receivers_cache.clear()
            signal.sender_receivers_cache.update(cache)


class Command(BaseCommand):
    """
    Seed a fresh database (used by the workspace docker compose on every start; safe to rerun).

    1. ``--fixture``: a ``dumpdata`` JSON file, loaded only while the database has no users, so a restart
       never loads it twice or overwrites later changes.
    2. A superuser (``DJANGO_SUPERUSER_EMAIL`` / ``DJANGO_SUPERUSER_PASSWORD``), created if missing and put in
       the first company so the app has data to show. An existing user is promoted; its password is kept.
    3. ``STOREFRONT_BASE_URL`` / ``STOREFRONT_WEBHOOK_SECRET``, when set, are written to every storefront
       connection, so a loaded snapshot points at this environment's m18.
    """

    help = 'Load a data snapshot into an empty database and make sure a superuser exists'

    def add_arguments(self, parser):
        parser.add_argument('--fixture', help='dumpdata JSON to load into an empty database (skipped if missing)')

    def handle(self, *args, **options):
        User = get_user_model()
        fixture = options['fixture']
        if fixture and not os.path.exists(fixture):
            self.stdout.write(f'No fixture at {fixture}; nothing to load.')
        elif fixture and User.objects.exists():
            self.stdout.write('Database already has users; fixture not loaded.')
        elif fixture:
            from inventory.locations import ensure_default_locations

            with transaction.atomic():
                with model_signals_muted():
                    call_command('loaddata', fixture, stdout=self.stdout)
                # Default locations come from post_save receivers, which were muted for the load. Same
                # transaction: if this fails, the snapshot is not left loaded without them.
                for company in Company.objects.all():
                    ensure_default_locations(company)

        email = os.environ.get('DJANGO_SUPERUSER_EMAIL')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
        if email:
            self._superuser(User, email, password)

        base_url = os.environ.get('STOREFRONT_BASE_URL')
        secret = os.environ.get('STOREFRONT_WEBHOOK_SECRET')
        if base_url or secret:
            self._storefront(base_url, secret)

    def _superuser(self, User, email, password):
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            if not password:
                raise CommandError('Set DJANGO_SUPERUSER_PASSWORD to create the superuser.')
            User.objects.create_superuser(email=email, password=password, company=Company.objects.order_by('pk').first())
            self.stdout.write(f'Created superuser {email}.')
        elif not (user.is_staff and user.is_superuser and user.is_active):
            user.is_staff = user.is_superuser = user.is_active = True
            user.save(update_fields=['is_staff', 'is_superuser', 'is_active'])
            self.stdout.write(f'Made {email} a superuser.')

    def _storefront(self, base_url, secret):
        from sync.models import StorefrontConnection

        for connection in StorefrontConnection.objects.all():
            if base_url:
                connection.base_url = base_url
            if secret:
                connection.webhook_secret = secret
            connection.save()
            self.stdout.write(f'Storefront connection {connection.pk} updated from the environment.')
