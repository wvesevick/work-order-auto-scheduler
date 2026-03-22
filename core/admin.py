from django.contrib import admin
from .models import Customer, Lease, Machine, WorkOrder, Lease_History, Machine_History, Tasks, Company_Stats

class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ('ticket_number', 'activity_type', 'address', 'customer_availability', 'status')

admin.site.register(Customer)
admin.site.register(Lease)
admin.site.register(Machine)
admin.site.register(WorkOrder, WorkOrderAdmin)
admin.site.register(Lease_History)
admin.site.register(Machine_History)
admin.site.register(Tasks)
admin.site.register(Company_Stats)