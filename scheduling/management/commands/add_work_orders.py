import csv
from django.core.management.base import BaseCommand
from core.models import WorkOrder

class Command(BaseCommand):
    help = 'Add work orders from a CSV file'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        with open(csv_file, 'r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                WorkOrder.objects.create(
                    ticket_number=row['ticket_number'],
                    activity_type=row['activity_type'],
                    address=row['address'],
                    customer_availability=row['customer_availability'],
                    status=row['status']
                )
        self.stdout.write(self.style.SUCCESS('Work orders added successfully'))
        