from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from datetime import datetime, timedelta, time
from scheduling.serializers import TechnicianScheduleSerializer
from scheduling.utils import Scheduler
from scheduling.models import Technician, ScheduleEntry
from core.models import WorkOrder
from django.contrib import admin
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from collections import defaultdict
from django.db.models import Max
from django.db import IntegrityError, transaction
import csv
from io import TextIOWrapper
from django.utils import timezone
from django.forms.models import model_to_dict
import os
import requests

logger = logging.getLogger(__name__)


def _template_context():
    return {}

class WorkOrderAdmin(admin.ModelAdmin):
    def schedule_view(self, request):
        return render(request, 'admin/schedule_view.html', {})
    
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        urls.insert(0, path('schedule-view/', self.schedule_view, name='schedule_view'))
        return urls

class ScheduleAPIView(APIView):
    def get(self, request):
        start_date_str = timezone.localdate().strftime('%Y-%m-%d')
        weeks = 4
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {"error": "Invalid start date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST
            )

        now = timezone.now()
        WorkOrder.objects.filter(
            status='scheduled',
            scheduled_at__lt=now - timedelta(days=1)
        ).update(
            status='complete',
            completed_at=now
        )

        scheduler = Scheduler()
        travel_time_cache = {}

        technicians = Technician.objects.all()
        all_technicians = []
        for tech in technicians:
            tech.schedule = []
            entries = ScheduleEntry.objects.filter(technician=tech)
            for entry in entries:
                tech.schedule.append((
                    entry.work_order,
                    entry.start_time,
                    entry.end_time,
                    entry.location,
                    entry.travel_to_next
                ))
            tech.daily_hours = defaultdict(float)
            for _, start, _, _, _ in tech.schedule:
                date_str = start.date().strftime('%Y-%m-%d')
                tech.daily_hours[date_str] += 2
            all_technicians.append(tech)

        if not any(tech.schedule for tech in all_technicians):
            work_orders = WorkOrder.objects.filter(status='pending')
            scheduled_work_orders = set()
            for week in range(weeks):
                week_start = start_date + timedelta(days=week * 7)
                logger.info(f"Processing week {week + 1} starting on {week_start}")
                week_technicians = list(Technician.objects.all())
                for tech in week_technicians:
                    tech.schedule = []
                scheduled_technicians, week_travel_time_cache = scheduler.schedule(
                    [wo for wo in work_orders if wo.ticket_number not in scheduled_work_orders],
                    week_technicians,
                    week_start
                )
                travel_time_cache.update(week_travel_time_cache)

                for tech in scheduled_technicians:
                    if not tech.schedule:
                        continue
                    schedule_by_date = defaultdict(list)
                    for entry in tech.schedule:
                        date = entry[1].date()
                        schedule_by_date[date].append(entry)

                    for date, daily_entries in schedule_by_date.items():
                        unique_schedule = []
                        seen_addresses = set()
                        for entry in daily_entries:
                            wo, start, end, location = entry
                            if location not in seen_addresses:
                                seen_addresses.add(location)
                                unique_schedule.append((wo, start, end, location))

                        for i, entry in enumerate(daily_entries):
                            wo, start, end, location = entry
                            scheduled_work_orders.add(wo.ticket_number)
                            if i == 0:
                                prev_loc = tech.home_address
                                next_loc = location
                                cache_key = f"{prev_loc}|{next_loc}"
                                if cache_key not in travel_time_cache:
                                    travel_time = scheduler.get_travel_time(prev_loc, next_loc)
                                    travel_time_cache[cache_key] = travel_time
                                    travel_time_cache[f"{next_loc}|{prev_loc}"] = travel_time
                                    scheduler.save_travel_times(travel_time_cache)
                                travel_time = travel_time_cache.get(cache_key, 5)
                            else:
                                next_loc = tech.home_address if i + 1 >= len(unique_schedule) else unique_schedule[i + 1][3]
                                cache_key = f"{location}|{next_loc}"
                                if cache_key not in travel_time_cache:
                                    travel_time = scheduler.get_travel_time(location, next_loc)
                                    travel_time_cache[cache_key] = travel_time
                                    travel_time_cache[f"{next_loc}|{location}"] = travel_time
                                    scheduler.save_travel_times(travel_time_cache)
                                travel_time = travel_time_cache.get(cache_key, 5)

                            tech.schedule[i] = (wo, start, end, location, travel_time)
                            ScheduleEntry.objects.update_or_create(
                                technician=tech,
                                work_order=wo,
                                start_time=start,
                                defaults={
                                    'end_time': end,
                                    'location': location,
                                    'travel_to_next': travel_time
                                }
                            )

        all_technicians = []
        for tech in technicians:
            tech.schedule = []
            entries = ScheduleEntry.objects.filter(technician=tech).order_by('start_time')
            schedule_by_date = defaultdict(list)
            for entry in entries:
                date = entry.start_time.date()
                schedule_by_date[date].append(entry)

            for date, daily_entries in schedule_by_date.items():
                unique_schedule = []
                seen_addresses = set()
                for entry in daily_entries:
                    if entry.location not in seen_addresses:
                        seen_addresses.add(entry.location)
                        unique_schedule.append(entry)

                for i, entry in enumerate(daily_entries):
                    location = entry.location
                    current_unique_index = None
                    for j, e in enumerate(unique_schedule):
                        if e.location == location and e.start_time.date() == entry.start_time.date():
                            current_unique_index = j
                            break
                    if i == 0:
                        prev_loc = tech.home_address
                        next_loc = location
                        cache_key = f"{prev_loc}|{next_loc}"
                        if cache_key not in travel_time_cache:
                            travel_time = scheduler.get_travel_time(prev_loc, next_loc)
                            travel_time_cache[cache_key] = travel_time
                            travel_time_cache[f"{next_loc}|{prev_loc}"] = travel_time
                            scheduler.save_travel_times(travel_time_cache)
                        travel_time = travel_time_cache.get(cache_key, 5)
                    else:
                        next_loc = tech.home_address if current_unique_index + 1 >= len(unique_schedule) else unique_schedule[current_unique_index + 1].location
                        cache_key = f"{location}|{next_loc}"
                        if cache_key not in travel_time_cache:
                            travel_time = scheduler.get_travel_time(location, next_loc)
                            travel_time_cache[cache_key] = travel_time
                            travel_time_cache[f"{next_loc}|{location}"] = travel_time
                            scheduler.save_travel_times(travel_time_cache)
                        travel_time = travel_time_cache.get(cache_key, 5)

                    entry.travel_to_next = travel_time
                    entry.save()
                    tech.schedule.append((
                        entry.work_order,
                        entry.start_time,
                        entry.end_time,
                        entry.location,
                        entry.travel_to_next
                    ))

            tech.daily_hours = defaultdict(float)
            for _, start, _, _, _ in tech.schedule:
                date_str = start.date().strftime('%Y-%m-%d')
                tech.daily_hours[date_str] += 2
            all_technicians.append(tech)

        serializer = TechnicianScheduleSerializer(all_technicians, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class TechniciansAPIView(APIView):
    def get(self, request):
        technicians = Technician.objects.all()
        data = []
        for tech in technicians:
            data.append({
                'name': tech.name,
                'home_address': tech.home_address,
                'activity_type': tech.activity_type,
                'work_days': tech.work_days or 'Not Set',
            })
        return Response(data, status=status.HTTP_200_OK)

@csrf_exempt
def add_technician(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        name = data.get('name')
        home_address = data.get('home_address')
        activity_type = data.get('activity_type')
        work_days = data.get('work_days')

        if not all([name, home_address, activity_type]):
            return JsonResponse({'status': 'error', 'message': 'Missing required fields (name, home_address, activity_type)'}, status=400)

        try:
            if Technician.objects.filter(name=name).exists():
                return JsonResponse({'status': 'error', 'message': 'A technician with this name already exists'}, status=400)

            technician = Technician.objects.create(
                name=name,
                home_address=home_address,
                activity_type=activity_type,
                work_days=work_days,
                working_start_time=time(8, 0),
                working_end_time=time(16, 0)
            )
            response = JsonResponse({'status': 'success', 'message': 'Technician added successfully'})
            response['X-Cache-Invalidate'] = 'true'
            return response
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def update_technician(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        name = data.get('name')
        new_name = data.get('new_name')
        home_address = data.get('home_address')
        activity_type = data.get('activity_type')
        work_days = data.get('work_days')

        if not all([name, new_name, home_address, activity_type, work_days]):
            return JsonResponse({'status': 'error', 'message': 'Missing required fields'}, status=400)

        try:
            technician = Technician.objects.get(name=name)
            if name != new_name and Technician.objects.filter(name=new_name).exists():
                return JsonResponse({'status': 'error', 'message': 'A technician with the new name already exists'}, status=400)
            technician.name = new_name
            technician.home_address = home_address
            technician.activity_type = activity_type
            technician.work_days = work_days
            technician.save()
            response = JsonResponse({'status': 'success', 'message': 'Technician updated successfully'})
            response['X-Cache-Invalidate'] = 'true'
            return response
        except Technician.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Technician not found'}, status=404)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

def get_schedule(self, obj):
    return [{
        'work_order': {
            'ticket_number': entry[0].ticket_number,
            'activity_type': entry[0].activity_type,
            'address': entry[0].address,
            'customer_availability': entry[0].customer_availability,
            'status': entry[0].status,
            'site_name': entry[0].site_name or '',
            'created_at': entry[0].created_at.isoformat(),
            'completed_at': entry[0].completed_at.isoformat() if entry[0].completed_at else None
        },
        'start_time': entry[1].isoformat(),
        'end_time': entry[2].isoformat(),
        'location': entry[3],
        'travel_to_next': entry[4]
    } for entry in obj.schedule]

def schedule_view(request):
    return render(request, 'scheduling/index.html', _template_context())

class WorkOrdersAPIView(APIView):
    def get(self, request):
        work_orders = WorkOrder.objects.all()
        data = [
            {
                'ticket_number': wo.ticket_number,
                'activity_type': wo.activity_type,
                'status': wo.status,
                'customer_availability': wo.customer_availability,
                'address': wo.address,
                'site_name': wo.site_name,
                'notes': wo.notes,
                'created_at': wo.created_at.isoformat(),
                'scheduled_at': wo.scheduled_at.isoformat() if wo.scheduled_at else None,
                'completed_at': wo.completed_at.isoformat() if wo.completed_at else None
            }
            for wo in work_orders
        ]
        return Response(data, status=status.HTTP_200_OK)

def work_orders_view(request):
    return render(request, 'scheduling/work_orders.html', _template_context())


def technicians_view(request):
    return render(request, 'scheduling/tech.html', _template_context())

@csrf_exempt
def add_work_order(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        
        max_wo_number = WorkOrder.objects.aggregate(Max('ticket_number'))['ticket_number__max']
        if max_wo_number and max_wo_number.startswith('WO-'):
            try:
                current_max = int(max_wo_number.split('-')[1])
            except (ValueError, IndexError):
                current_max = 0
        else:
            current_max = 0
        new_wo_number = current_max + 1
        unique_ticket_number = f"WO-{new_wo_number}"

        while WorkOrder.objects.filter(ticket_number=unique_ticket_number).exists():
            new_wo_number += 1
            unique_ticket_number = f"WO-{new_wo_number}"

        try:
            new_wo = WorkOrder.objects.create(
                ticket_number=unique_ticket_number,
                activity_type=data['activity_type'],
                address=data['address'],
                customer_availability=data['customer_availability'],
                site_name=data.get('site_name', ''),
                notes=data.get('notes', ''),
                status='pending'
            )
        except IntegrityError:
            return JsonResponse({'status': 'error', 'message': 'Failed to create unique work order number'}, status=500)

        scheduler = Scheduler()
        technicians = list(Technician.objects.all())
        today = datetime.now().date()
        days_until_sunday = (6 - today.weekday()) % 7
        start_date = today + timedelta(days=days_until_sunday)

        initial_schedules = {tech: defaultdict(dict) for tech in technicians}
        pending_work_orders = []
        for entry in ScheduleEntry.objects.all():
            tech = entry.technician
            wo = entry.work_order
            if wo.status in ['scheduled', 'complete']:
                date = entry.start_time.date()
                slot_start = entry.start_time.time()
                slot_end = entry.end_time.time()
                initial_schedules[tech][date][(slot_start, slot_end)] = (
                    entry.work_order,
                    entry.start_time,
                    entry.end_time,
                    entry.location
                )
            elif wo.status == 'pending':
                pending_work_orders.append(wo)

        work_orders_to_schedule = [new_wo] + pending_work_orders

        scheduled_technicians, travel_time_cache = scheduler.schedule(
            work_orders_to_schedule,
            technicians,
            start_date,
            initial_schedules=initial_schedules
        )

        ScheduleEntry.objects.filter(work_order__status='pending').delete()
        for tech in scheduled_technicians:
            for wo, start, end, location in tech.schedule:
                if wo.ticket_number == new_wo.ticket_number or wo.status == 'pending':
                    next_loc = tech.home_address
                    tech_entries = ScheduleEntry.objects.filter(technician=tech).order_by('start_time')
                    for i, e in enumerate(tech_entries):
                        if e.start_time > start:
                            next_loc = e.location
                            break
                    travel_time = scheduler.get_travel_time(location, next_loc)
                    ScheduleEntry.objects.update_or_create(
                        technician=tech,
                        work_order=wo,
                        start_time=start,
                        defaults={
                            'end_time': end,
                            'location': location,
                            'travel_to_next': travel_time
                        }
                    )
                    wo.scheduled_at = timezone.make_aware(start)
                    wo.save()

        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def create_work_order(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        
        max_wo_number = WorkOrder.objects.aggregate(Max('ticket_number'))['ticket_number__max']
        current_max = int(max_wo_number.split('-')[1]) if max_wo_number and max_wo_number.startswith('WO-') else 0
        new_wo_number = current_max + 1
        unique_ticket_number = f"WO-{new_wo_number}"
        while WorkOrder.objects.filter(ticket_number=unique_ticket_number).exists():
            new_wo_number += 1
            unique_ticket_number = f"WO-{new_wo_number}"

        try:
            new_wo = WorkOrder.objects.create(
                ticket_number=unique_ticket_number,
                activity_type=data['activity_type'],
                address=data['address'],
                customer_availability=data['customer_availability'],
                site_name=data.get('site_name', ''),
                notes=data.get('notes', ''),
                status='submitted'
            )
            return JsonResponse({'status': 'success', 'ticket_number': new_wo.ticket_number})
        except IntegrityError:
            return JsonResponse({'status': 'error', 'message': 'Failed to create work order'}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def upload_csv_work_orders(request):
    if request.method == 'POST':
        try:
            csv_file = request.FILES['csv_file']
            if not csv_file.name.endswith('.csv'):
                return JsonResponse({'status': 'error', 'message': 'File must be a CSV'}, status=400)

            csv_data = TextIOWrapper(csv_file.file, encoding='utf-8')
            reader = csv.DictReader(csv_data)
            required_fields = {'activity_type', 'address', 'customer_availability'}
            if not all(field in reader.fieldnames for field in required_fields):
                return JsonResponse({'status': 'error', 'message': 'CSV missing required fields: activity_type, address, customer_availability'}, status=400)

            max_wo_number = WorkOrder.objects.aggregate(Max('ticket_number'))['ticket_number__max']
            current_max = int(max_wo_number.split('-')[1]) if max_wo_number and max_wo_number.startswith('WO-') else 0
            new_wo_number = current_max + 1

            created_count = 0
            for row in reader:
                unique_ticket_number = f"WO-{new_wo_number}"
                while WorkOrder.objects.filter(ticket_number=unique_ticket_number).exists():
                    new_wo_number += 1
                    unique_ticket_number = f"WO-{new_wo_number}"
                
                WorkOrder.objects.create(
                    ticket_number=unique_ticket_number,
                    activity_type=row['activity_type'],
                    address=row['address'],
                    customer_availability=row['customer_availability'],
                    site_name=row.get('site_name', ''),
                    notes=row.get('notes', ''),
                    status='submitted'
                )
                created_count += 1
                new_wo_number += 1

            return JsonResponse({'status': 'success', 'message': f'{created_count} work orders created'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def update_work_order_status(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        ticket_number = data.get('ticket_number')
        activity_type = data.get('activity_type')
        new_status = data.get('status')
        customer_availability = data.get('customer_availability')
        address = data.get('address')
        site_name = data.get('site_name')
        notes = data.get('notes')

        if not ticket_number:
            return JsonResponse({'status': 'error', 'message': 'Missing ticket_number'}, status=400)

        try:
            work_order = WorkOrder.objects.get(ticket_number=ticket_number)

            if work_order.status == 'submitted' and new_status and new_status != 'submitted':
                return JsonResponse({'status': 'error', 'message': 'Cannot change status from submitted directly'}, status=400)

            if activity_type:
                work_order.activity_type = activity_type
            if customer_availability:
                work_order.customer_availability = customer_availability
            if address:
                work_order.address = address
            if site_name is not None:
                work_order.site_name = site_name
            if notes is not None:
                work_order.notes = notes

            is_non_status_changed = (
                (activity_type and activity_type != work_order.activity_type) or
                (customer_availability and customer_availability != work_order.customer_availability) or
                (address and address != work_order.address) or
                (site_name is not None and site_name != (work_order.site_name or '')) or
                (notes is not None and notes != (work_order.notes or ''))
            )

            if is_non_status_changed:
                work_order.status = 'submitted'
                work_order.scheduled_at = None
                ScheduleEntry.objects.filter(work_order=work_order).delete()
            elif new_status:
                work_order.status = new_status
                if new_status == 'submitted':
                    work_order.scheduled_at = None
                    ScheduleEntry.objects.filter(work_order=work_order).delete()
                    work_order.save()  # Fixed typo: was 'work_order.s'

            if work_order.status == 'complete' and not work_order.completed_at:
                work_order.completed_at = timezone.now()
            elif work_order.status != 'complete':
                work_order.completed_at = None

            work_order.save()
            response = JsonResponse({'status': 'success', 'ticket_number': ticket_number, 'new_status': work_order.status})
            response['X-Cache-Invalidate'] = 'true'
            return response
        except WorkOrder.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Work order not found'}, status=404)
        except Exception as e:
            logger.error(f"Error updating work order: {str(e)}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def schedule_all_work_orders(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body) if request.body else {}
            display_date = data.get('display_date', timezone.now().strftime('%Y-%m-%d'))
            logger.info(f"Approving all pending work orders to scheduled for date: {display_date}")

            display_date_obj = datetime.strptime(display_date, '%Y-%m-%d').date()
            entries = ScheduleEntry.objects.filter(
                work_order__status='pending',
                start_time__date=display_date_obj
            )
            scheduled_count = 0

            for entry in entries:
                work_order = entry.work_order
                work_order.status = 'scheduled'
                work_order.scheduled_at = entry.start_time
                work_order.save()
                scheduled_count += 1
                logger.info(f"Approved WO-{work_order.ticket_number} to scheduled on {entry.start_time}")

            response = JsonResponse({'status': 'success', 'message': f'{scheduled_count} work orders scheduled'})
            response['X-Cache-Invalidate'] = 'true'
            return response
        except Exception as e:
            logger.error(f"Error in schedule_all_work_orders: {str(e)}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def schedule_selected_work_orders(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            ticket_numbers = data.get('ticket_numbers', [])
            logger.debug(f"Received ticket numbers: {ticket_numbers}")
            work_orders = WorkOrder.objects.filter(ticket_number__in=ticket_numbers, status='submitted')
            if not work_orders:
                logger.warning(f"No submitted work orders found for tickets: {ticket_numbers}")
                return JsonResponse({
                    'status': 'error',
                    'message': f'No submitted work orders found for tickets: {", ".join(ticket_numbers)}'
                }, status=400)

            logger.debug(f"Work orders to schedule: {[wo.ticket_number for wo in work_orders]}")
            scheduler = Scheduler()
            technicians = list(Technician.objects.all())
            start_date = timezone.localdate()

            initial_schedules = {tech: defaultdict(dict) for tech in technicians}
            pending_work_orders = []
            for entry in ScheduleEntry.objects.all():
                tech = entry.technician
                wo = entry.work_order
                if wo.status in ['scheduled', 'complete']:
                    date = entry.start_time.date()
                    slot_start = entry.start_time.time()
                    slot_end = entry.end_time.time()
                    initial_schedules[tech][date][(slot_start, slot_end)] = (
                        entry.work_order,
                        entry.start_time,
                        entry.end_time,
                        entry.location
                    )
                elif wo.status == 'pending':
                    pending_work_orders.append(wo)

            work_orders_to_schedule = list(work_orders) + pending_work_orders

            logger.debug(f"Loaded initial schedules: {[(tech.name, list(dates.keys())) for tech, dates in initial_schedules.items()]}")
            logger.info("Starting scheduling process")
            
            with transaction.atomic():
                scheduled_technicians, travel_time_cache = scheduler.schedule(
                    work_orders_to_schedule,
                    technicians,
                    start_date,
                    initial_schedules=initial_schedules
                )

                ScheduleEntry.objects.filter(work_order__status='pending').delete()
                scheduled_count = 0
                scheduled_tickets = []
                unscheduled_tickets = list(ticket_numbers)
                schedule_entries_to_create = []

                for tech in scheduled_technicians:
                    if not tech.schedule:
                        logger.debug(f"No schedule assigned for {tech.name}")
                        continue
                    schedule_by_date = defaultdict(list)
                    for assignment in tech.schedule:
                        wo, start, end, location = assignment
                        date = start.date()
                        schedule_by_date[date].append((wo, start, end, location))

                    for date, daily_entries in schedule_by_date.items():
                        unique_schedule = []
                        seen_addresses = set()
                        for entry in daily_entries:
                            wo, start, end, location = entry
                            if location not in seen_addresses:
                                seen_addresses.add(location)
                                unique_schedule.append((wo, start, end, location))

                        for i, (wo, start, end, location) in enumerate(daily_entries):
                            if wo not in work_orders_to_schedule:
                                logger.debug(f"Skipping locked WO-{wo.ticket_number} for {tech.name} on {start}")
                                continue
                            start_aware = timezone.make_aware(start)
                            existing_entry = ScheduleEntry.objects.filter(
                                technician=tech,
                                start_time=start_aware
                            ).exclude(work_order=wo).exists()
                            if existing_entry:
                                logger.warning(f"Slot {start_aware} for {tech.name} already occupied, skipping WO-{wo.ticket_number}")
                                continue
                            if i == 0:
                                prev_loc = tech.home_address
                                next_loc = location if i + 1 >= len(daily_entries) else daily_entries[i + 1][3]
                            else:
                                prev_loc = daily_entries[i - 1][3]
                                next_loc = tech.home_address if i + 1 >= len(daily_entries) else daily_entries[i + 1][3]
                            travel_time = scheduler.get_travel_time(location, next_loc) if i < len(daily_entries) - 1 else scheduler.get_travel_time(location, tech.home_address)
                            end_aware = timezone.make_aware(end)
                            schedule_entries_to_create.append({
                                'technician': tech,
                                'work_order': wo,
                                'start_time': start_aware,
                                'end_time': end_aware,
                                'location': location,
                                'travel_to_next': travel_time
                            })
                            if wo.ticket_number in ticket_numbers:
                                scheduled_count += 1
                                scheduled_tickets.append(wo.ticket_number)
                                if wo.ticket_number in unscheduled_tickets:
                                    unscheduled_tickets.remove(wo.ticket_number)
                                logger.info(f"Scheduled WO-{wo.ticket_number} with {tech.name} on {start_aware}")

                logger.debug(f"Creating {len(schedule_entries_to_create)} ScheduleEntry records")
                for entry_data in schedule_entries_to_create:
                    ScheduleEntry.objects.update_or_create(
                        technician=entry_data['technician'],
                        work_order=entry_data['work_order'],
                        start_time=entry_data['start_time'],
                        defaults={
                            'end_time': entry_data['end_time'],
                            'location': entry_data['location'],
                            'travel_to_next': entry_data['travel_to_next']
                        }
                    )
                    entry_data['work_order'].status = 'pending'
                    entry_data['work_order'].scheduled_at = entry_data['start_time']
                    entry_data['work_order'].save()

                for ticket in unscheduled_tickets:
                    try:
                        wo = WorkOrder.objects.get(ticket_number=ticket)
                        if wo.status != 'submitted':
                            wo.status = 'submitted'
                            wo.scheduled_at = None
                            ScheduleEntry.objects.filter(work_order=wo).delete()
                            wo.save()
                            logger.info(f"Reverted WO-{ticket} to submitted status")
                    except WorkOrder.DoesNotExist:
                        logger.warning(f"Work order {ticket} not found during status revert")

            message_parts = []
            if scheduled_tickets:
                message_parts.append(f"Successfully routed: {', '.join(scheduled_tickets)}")
            if unscheduled_tickets:
                message_parts.append(f"Could not route: {', '.join(unscheduled_tickets)}")
            message = '. '.join(message_parts) if message_parts else "No work orders were scheduled"

            if scheduled_count == 0 and not unscheduled_tickets:
                logger.error(f"No work orders scheduled. Tech schedules: {[(t.name, t.schedule) for t in scheduled_technicians]}")
                return JsonResponse({
                    'status': 'error',
                    'message': 'No work orders were scheduled'
                }, status=400)

            for wo in work_orders:
                wo.refresh_from_db()
                logger.debug(f"Post-scheduling DB check: WO-{wo.ticket_number} status={wo.status}, scheduled_at={wo.scheduled_at}")
            logger.info(f"Scheduled {scheduled_count} work orders. Message: {message}")
            response = JsonResponse({
                'status': 'success' if scheduled_count > 0 else 'partial',
                'message': message,
                'scheduled': scheduled_tickets,
                'unscheduled': unscheduled_tickets
            })
            response['X-Cache-Invalidate'] = 'true'
            return response
        except Exception as e:
            logger.error(f"Error in schedule_selected_work_orders: {str(e)}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

def schedule_api_view(request):
    technicians = Technician.objects.all()
    scheduler = Scheduler()
    travel_time_cache = scheduler.load_travel_times()

    schedule_data = []
    for tech in technicians:
        entries = ScheduleEntry.objects.filter(technician=tech).select_related('work_order').order_by('start_time')
        schedule_by_date = defaultdict(list)
        for entry in entries:
            date = entry.start_time.date()
            schedule_by_date[date].append(entry)

        updated_entries = []
        for date, daily_entries in schedule_by_date.items():
            unique_schedule = []
            seen_addresses = set()
            for entry in daily_entries:
                if entry.location not in seen_addresses:
                    seen_addresses.add(entry.location)
                    unique_schedule.append(entry)

            for i in range(len(unique_schedule)):
                entry = unique_schedule[i]
                location = entry.location

                if i == 0:
                    prev_loc = tech.home_address
                else:
                    prev_loc = unique_schedule[i - 1].location

                if i == len(unique_schedule) - 1:
                    next_loc = tech.home_address
                else:
                    next_loc = unique_schedule[i + 1].location

                cache_key = f"{location}|{next_loc}"
                if cache_key in travel_time_cache:
                    travel_time = travel_time_cache[cache_key]
                else:
                    travel_time = scheduler.get_travel_time(location, next_loc)
                    travel_time_cache[cache_key] = travel_time
                    travel_time_cache[f"{next_loc}|{location}"] = travel_time
                    scheduler.save_travel_times(travel_time_cache)

                entry.travel_to_next = travel_time
                entry.save()

                for daily_entry in daily_entries:
                    if daily_entry.location == entry.location and daily_entry.start_time.date() == entry.start_time.date():
                        daily_entry.travel_to_next = travel_time
                        daily_entry.save()
                        if daily_entry not in updated_entries:
                            updated_entries.append(daily_entry)

        schedule_entries = []
        for entry in updated_entries:
            start_local = timezone.localtime(entry.start_time)
            end_local = timezone.localtime(entry.end_time)
            wo_dict = model_to_dict(entry.work_order, fields=['ticket_number', 'activity_type', 'address', 'customer_availability', 'status', 'site_name'])
            schedule_entries.append({
                'work_order': wo_dict,
                'start_time': start_local.isoformat(),
                'end_time': end_local.isoformat(),
                'location': entry.location,
                'travel_to_next': entry.travel_to_next
            })

        schedule_data.append({
            'technician': tech.name,
            'home_address': tech.home_address,
            'schedule': schedule_entries
        })
    return JsonResponse(schedule_data, safe=False)


def _decode_polyline(polyline_str):
    if not polyline_str:
        return []

    coords = []
    index = 0
    lat = 0
    lng = 0
    length = len(polyline_str)

    while index < length:
        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append([lat / 1e5, lng / 1e5])

    return coords


@csrf_exempt
def route_preview(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON payload'}, status=400)

    stops = payload.get('stops', [])
    if not isinstance(stops, list) or len(stops) < 2:
        return JsonResponse({'status': 'error', 'message': 'At least two stops are required'}, status=400)

    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return JsonResponse({'status': 'fallback', 'message': 'Google route key not configured'})

    scheduler = Scheduler()
    if not scheduler.reserve_external_api_call():
        return JsonResponse({'status': 'fallback', 'message': 'Route API daily limit reached'})

    origin = stops[0]
    destination = stops[-1]
    waypoints = stops[1:-1]

    params = {
        'origin': origin,
        'destination': destination,
        'key': api_key,
    }
    if waypoints:
        params['waypoints'] = '|'.join(waypoints)

    try:
        response = requests.get(
            "https://maps.googleapis.com/maps/api/directions/json",
            params=params,
            timeout=10,
        )
        directions = response.json()
    except Exception as e:
        logger.error(f"Route preview request failed: {e}", exc_info=True)
        return JsonResponse({'status': 'fallback', 'message': 'Could not fetch route data'})

    if directions.get('status') != 'OK' or not directions.get('routes'):
        return JsonResponse({
            'status': 'fallback',
            'message': directions.get('status', 'No route available')
        })

    route = directions['routes'][0]
    encoded_polyline = route.get('overview_polyline', {}).get('points', '')
    geometry = _decode_polyline(encoded_polyline)

    legs = []
    for leg in route.get('legs', []):
        legs.append({
            'duration_seconds': leg.get('duration', {}).get('value', 0),
            'distance_meters': leg.get('distance', {}).get('value', 0),
        })

    return JsonResponse({
        'status': 'success',
        'geometry': geometry,
        'legs': legs,
    })

@csrf_exempt
def get_travel_time(request):
    if request.method == 'GET':
        origin = request.GET.get('origin')
        destination = request.GET.get('destination')
        if not origin or not destination:
            return JsonResponse({'status': 'error', 'message': 'Missing origin or destination'}, status=400)

        scheduler = Scheduler()
        travel_time_cache = scheduler.load_travel_times()
        cache_key = f"{origin}|{destination}"

        try:
            if cache_key in travel_time_cache:
                travel_time = travel_time_cache[cache_key]
                logger.debug(f"Using cached travel time for {cache_key}: {travel_time} minutes")
            else:
                travel_time = scheduler.get_travel_time(origin, destination)
                travel_time_cache[cache_key] = travel_time
                travel_time_cache[f"{destination}|{origin}"] = travel_time
                scheduler.save_travel_times(travel_time_cache)
                logger.debug(f"Fetched and cached new travel time for {cache_key}: {travel_time} minutes")

            return JsonResponse({'status': 'success', 'travel_time': travel_time})
        except Exception as e:
            logger.error(f"Error getting travel time: {str(e)}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def submit_selected_work_orders(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            ticket_numbers = data.get('ticket_numbers', [])
            logger.debug(f"Received ticket numbers to submit: {ticket_numbers}")
            
            if not ticket_numbers:
                logger.warning("No ticket numbers provided")
                return JsonResponse({'status': 'error', 'message': 'No work orders selected'}, status=400)
            
            work_orders = WorkOrder.objects.filter(ticket_number__in=ticket_numbers)
            updated_count = 0
            
            for wo in work_orders:
                wo.status = 'submitted'
                wo.scheduled_at = None
                wo.save()
                ScheduleEntry.objects.filter(work_order=wo).delete()
                updated_count += 1
                logger.info(f"Updated WO-{wo.ticket_number} status to 'submitted'")
            
            if updated_count == 0:
                logger.warning(f"No work orders found for tickets: {ticket_numbers}")
                return JsonResponse({'status': 'error', 'message': 'No work orders found to update'}, status=400)
            
            response = JsonResponse({'status': 'success', 'message': f'{updated_count} work orders submitted'})
            response['X-Cache-Invalidate'] = 'true'
            return response
        except Exception as e:
            logger.error(f"Error in submit_selected_work_orders: {str(e)}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@csrf_exempt
def assign_work_order(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            ticket_number = data.get('ticket_number')
            technician_name = data.get('technician')
            date_str = data.get('date')
            slot_start = data.get('slot_start')

            if not all([ticket_number, technician_name, date_str, slot_start]):
                return JsonResponse({'status': 'error', 'message': 'Missing required fields: ticket_number, technician, date, slot_start'}, status=400)

            try:
                work_order = WorkOrder.objects.get(ticket_number=ticket_number)
            except WorkOrder.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Work order not found'}, status=404)

            if work_order.status != 'submitted':
                return JsonResponse({'status': 'error', 'message': f'Work order must be in submitted status, current status: {work_order.status}'}, status=400)

            try:
                technician = Technician.objects.get(name=technician_name)
            except Technician.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Technician not found'}, status=404)

            if work_order.activity_type.lower().strip() != technician.activity_type.lower().strip():
                return JsonResponse({'status': 'error', 'message': 'Work order activity type does not match technician activity type'}, status=400)

            try:
                schedule_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({'status': 'error', 'message': 'Invalid date format, use YYYY-MM-DD'}, status=400)

            slot_mappings = {
                '08:00:00': (time(8, 0), time(10, 0)),
                '10:00:00': (time(10, 0), time(12, 0)),
                '12:00:00': (time(12, 0), time(14, 0)),
                '14:00:00': (time(14, 0), time(16, 0))
            }

            if slot_start not in slot_mappings:
                return JsonResponse({'status': 'error', 'message': 'Invalid slot start time'}, status=400)

            slot_start_time, slot_end_time = slot_mappings[slot_start]

            existing_entry = ScheduleEntry.objects.filter(
                technician=technician,
                start_time__date=schedule_date,
                start_time__time=slot_start_time
            ).exists()
            if existing_entry:
                return JsonResponse({'status': 'error', 'message': 'Selected slot is already occupied'}, status=400)

            scheduler = Scheduler()
            work_order_slots = scheduler.parse_availability(work_order.customer_availability)
            slot_matches = any(
                slot_date == schedule_date and
                slot_start_time >= cust_start and
                slot_end_time <= cust_end
                for slot_date, cust_start, cust_end in work_order_slots
            )
            if not slot_matches:
                return JsonResponse({'status': 'error', 'message': 'Work order availability does not match selected slot'}, status=400)

            entries = ScheduleEntry.objects.filter(technician=technician, start_time__date=schedule_date).order_by('start_time')
            prev_loc = technician.home_address
            next_loc = technician.home_address
            for i, entry in enumerate(entries):
                if entry.start_time.time() < slot_start_time:
                    prev_loc = entry.location
                elif entry.start_time.time() > slot_start_time and not next_loc:
                    next_loc = entry.location
                    break

            travel_time = scheduler.get_travel_time(prev_loc, work_order.address)

            start_datetime = timezone.make_aware(datetime.combine(schedule_date, slot_start_time))
            end_datetime = timezone.make_aware(datetime.combine(schedule_date, slot_end_time))
            ScheduleEntry.objects.create(
                technician=technician,
                work_order=work_order,
                start_time=start_datetime,
                end_time=end_datetime,
                location=work_order.address,
                travel_to_next=travel_time
            )

            work_order.status = 'scheduled'
            work_order.scheduled_at = start_datetime
            work_order.save()

            logger.info(f"Assigned WO-{ticket_number} to {technician_name} on {date_str} at {slot_start}")
            response = JsonResponse({'status': 'success', 'message': 'Work order assigned successfully'})
            response['X-Cache-Invalidate'] = 'true'
            return response
        except Exception as e:
            logger.error(f"Error in assign_work_order: {str(e)}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)
