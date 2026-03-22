from django.contrib import admin
from django.shortcuts import render
from django.urls import path
from datetime import datetime
import json
from .models import Technician, WorkOrderAssignment

class WorkOrderAssignmentAdmin(admin.ModelAdmin):
    list_display = ('work_order', 'technician', 'date', 'start_time', 'end_time')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('custom-schedule-view/', self.admin_site.admin_view(self.schedule_view), name='schedule-view'),
        ]
        return custom_urls + urls

    def schedule_view(self, request):
        # Fetch all assignments with related work orders and technicians
        assignments = WorkOrderAssignment.objects.all().select_related('work_order', 'technician')
        items = []
        groups = []
        tech_ids = set()

        # Build items and groups for the timeline
        for assignment in assignments:
            wo = assignment.work_order
            tech = assignment.technician
            start = datetime.combine(assignment.date, assignment.start_time)
            end = datetime.combine(assignment.date, assignment.end_time)

            # Add technician to groups if not already included
            if tech.id not in tech_ids:
                tech_ids.add(tech.id)
                groups.append({
                    'id': tech.id,
                    'content': tech.name,
                })

            # Add assignment to items (without 'style' for Vis.js compatibility)
            items.append({
                'id': assignment.id,
                'group': tech.id,
                'content': f"{wo.ticket_number} at {wo.address}",
                'start': start.strftime('%Y-%m-%dT%H:%M:%S'),
                'end': end.strftime('%Y-%m-%dT%H:%M:%S'),
                'wo_number': wo.ticket_number,  # Add WO#
                'address': wo.address,          # Add address
                'tech_name': tech.name  # For template display
            })

        # Context includes both raw data for template loops and JSON for JavaScript
        context = {
            'items': items,  # For Django template loops
            'groups': groups,
            'items_json': json.dumps(items),  # For Vis.js DataSet
            'groups_json': json.dumps(groups),
            'title': 'Technician Schedules'
        }
        return render(request, 'admin/schedule_view.html', context)

admin.site.register(Technician)
admin.site.register(WorkOrderAssignment, WorkOrderAssignmentAdmin)