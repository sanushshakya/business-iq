from django.core.management.base import BaseCommand

from sync.stock import mark_all


class Command(BaseCommand):
    help = "Queue every storefront-linked product's online stock to be sent (first rollout, or after a fix)."

    def handle(self, *args, **options):
        count = mark_all()
        self.stdout.write(f'Queued {count} linked product(s); the 15-second sweep sends them.')
