# scheduling/models.py
from django.db import models

class Technician(models.Model):
    name = models.CharField(max_length=100)
    activity_type = models.CharField(
        max_length=50,
        choices=[
            ('PM', 'PM'),
            ('Service', 'Service'),
            ('Ice', 'Ice'),
            ('Installation', 'Installation'),
        ],
        default='Service'
    )
    working_start_time = models.TimeField(
        default="08:00",
        help_text="Start of working hours (e.g., 10:00 for 10 AM)"
    )
    working_end_time = models.TimeField(
        default="16:00",
        help_text="End of working hours (e.g., 17:00 for 5 PM)"
    )
    home_address = models.CharField(max_length=200, blank=True, default="Unknown Address")
    work_days = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'technicians'

class WorkOrderAssignment(models.Model):
    work_order = models.ForeignKey(
        'core.WorkOrder',
        on_delete=models.CASCADE,
        related_name='assignments'
    )
    technician = models.ForeignKey(
        Technician,
        on_delete=models.CASCADE,
        related_name='assignments'
    )
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"WO{self.work_order.id} assigned to {self.technician.name} on {self.date}"

    class Meta:
        db_table = 'work_order_assignments'

class ScheduleEntry(models.Model):
    technician = models.ForeignKey(Technician, on_delete=models.CASCADE, related_name='schedule_entries')
    work_order = models.ForeignKey('core.WorkOrder', on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length=200)
    travel_to_next = models.FloatField(default=5)

    def __str__(self):
        return f"{self.technician.name} - {self.work_order.ticket_number} - {self.start_time}"

    class Meta:
        db_table = 'schedule_entries'


class ExternalAPICallUsage(models.Model):
    service = models.CharField(max_length=64)
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'external_api_call_usage'
        unique_together = ('service', 'day')

    def __str__(self):
        return f"{self.service} {self.day}: {self.count}"
