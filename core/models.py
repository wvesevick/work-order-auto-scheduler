from django.db import models

class Customer(models.Model):
    customer_id = models.AutoField(primary_key=True)  # Matches customers.customer_id
    customer_number = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    billing_address = models.TextField()
    total_billing_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def total_annual_billing(self):
        total = 0
        for lease in self.lease_set.all():
            if lease.billing_cycle == 'monthly':
                total += lease.total_billing_amount * 12
            elif lease.billing_cycle == 'annual':
                total += lease.total_billing_amount
            elif lease.billing_cycle == 'seasonal':
                if lease.first_billing_date and lease.final_billing_date:
                    # Calculate the number of months (approximate)
                    months = (lease.final_billing_date - lease.first_billing_date).days / 30
                    total += lease.total_billing_amount * months
       
        return total
    class Meta:
        db_table = 'customers'

    def __str__(self):
        return self.customer_number

class Lease(models.Model):
    lease_id = models.AutoField(primary_key=True)  # Matches leases.lease_id
    lease_number = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    address = models.TextField()
    total_billing_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    first_billing_date = models.DateField(null=True, blank=True)
    final_billing_date = models.DateField(null=True, blank=True)
    billing_cycle = models.CharField(
        max_length=20,
        choices=[
            ('monthly', 'Monthly'),
            ('annual', 'Annual'),
            ('seasonal', 'Seasonal'),
        ],
        default='monthly'
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'leases'

    def __str__(self):
        cycle_display = self.get_billing_cycle_display()
        return f"L-{self.lease_number} - Full Service - {cycle_display}"


class Machine(models.Model):
    machine_id = models.AutoField(primary_key=True)  # Matches machines.machine_id
    said_number = models.CharField(max_length=50, unique=True)
    model = models.CharField(max_length=100)
    serial_number = models.CharField(max_length=100, unique=True)
    machine_type = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=[
        ('installed', 'Installed'),
        ('not_ready', 'Not Ready'),
        ('ready', 'Ready'),
        ('sold', 'Sold'),
        ('decommissioned', 'Decommissioned')
    ])
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, null=True)
    automation_flag = models.BooleanField(default=False)

    class Meta:
        db_table = 'machines'

    def __str__(self):
        return self.said_number
    
class WorkOrder(models.Model):
    work_order_id = models.AutoField(primary_key=True)
    ticket_number = models.CharField(max_length=50, unique=True)
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, null=True)
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, null=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ('submitted', 'Submitted'),
            ('pending', 'Pending'),
            ('scheduled', 'Scheduled'),
            ('complete', 'Complete')
        ],
        default='submitted'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    tech_notes = models.TextField(null=True, blank=True)
    automated = models.BooleanField(default=False)
    site_name = models.CharField(max_length=50, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    address = models.CharField(max_length=200, blank=True)
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
    customer_availability = models.CharField(
        max_length=200,
        blank=True,
        help_text="Customer availability (e.g., '8am-2pm Mon, Wed')"
    )

    class Meta:
        db_table = 'work_orders'

    def __str__(self):
        return self.ticket_number
       
class Lease_History(models.Model):
    history_id = models.AutoField(primary_key=True)
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=50)
    event_date = models.DateTimeField(auto_now_add=True)
    details = models.TextField(null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, null=True, blank=True)
    automated = models.BooleanField(default=False)

    class Meta:
            db_table = 'lease_history'

    def __str__(self):
            return f"{self.lease.lease_number} - {self.event_type}"


class Machine_History(models.Model):
    history_id = models.AutoField(primary_key=True)
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=50)
    event_date = models.DateTimeField(auto_now_add=True)
    details = models.TextField(null=True, blank=True)
    billing_amount_change = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, null=True, blank=True)
    automated = models.BooleanField(default=False)

    class Meta:
        db_table = 'machine_history'

    def __str__(self):
        return f"{self.machine.said_number} - {self.event_type}"


class Tasks(models.Model):
    task_id = models.AutoField(primary_key=True)
    task_type = models.CharField(max_length=50)
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, null=True, blank=True)
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=20, default='pending')
    scheduled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    details = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'tasks'

    def __str__(self):
        return f"{self.task_type} - {self.status}"


class Company_Stats(models.Model):
    stat_id = models.AutoField(primary_key=True)
    stat_type = models.CharField(max_length=50)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    last_updated = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'company_stats'

    def __str__(self):
        return f"{self.stat_type}: {self.value}"
    

class Billing(models.Model):
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE)
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'billing'

    def __str__(self):
        return f"Billing for Lease {self.lease.id} on {self.date}"
