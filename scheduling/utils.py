import os
import json
import time
from datetime import datetime, timedelta, date, time as datetime_time
from collections import defaultdict
import requests
import pytz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
from itertools import combinations

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from scheduling.models import Technician, WorkOrderAssignment, ExternalAPICallUsage
from core.models import WorkOrder

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self, stdout=None, style=None):
        self.stdout = stdout
        self.style = style
        self.cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'travel_times.json')
        self.SLOTS = [
            (datetime_time(8, 0), datetime_time(10, 0)),
            (datetime_time(10, 0), datetime_time(12, 0)),
            (datetime_time(12, 0), datetime_time(14, 0)),
            (datetime_time(14, 0), datetime_time(16, 0))
        ]

    def log(self, message, level='INFO'):
        if self.stdout and hasattr(self.stdout, 'write'):
            if level == 'ERROR' and self.style:
                self.stdout.write(self.style.ERROR(message))
            elif level == 'WARNING' and self.style:
                self.stdout.write(self.style.WARNING(message))
            else:
                self.stdout.write(message)
        else:
            if level == 'ERROR':
                logger.error(message)
            elif level == 'WARNING':
                logger.warning(message)
            else:
                logger.info(message)

    def load_travel_times(self):
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    cache = json.load(f)
                    return cache
        except Exception as e:
            self.log(f"Error loading travel times cache: {e}", level='ERROR')
        return {}

    def save_travel_times(self, travel_times):
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(travel_times, f, indent=2)
        except Exception as e:
            self.log(f"Error saving travel times cache: {e}", level='ERROR')

    def _fallback_travel_time(self):
        fallback = getattr(settings, "GOOGLE_MAPS_DEFAULT_TRAVEL_MINUTES", 15)
        try:
            fallback = int(fallback)
        except (TypeError, ValueError):
            fallback = 15
        return max(fallback, 5)

    def _reserve_external_api_call(self):
        daily_limit = getattr(settings, "GOOGLE_MAPS_DAILY_CALL_LIMIT", 0)
        try:
            daily_limit = int(daily_limit)
        except (TypeError, ValueError):
            daily_limit = 0

        if daily_limit <= 0:
            return False

        today = timezone.localdate()
        try:
            with transaction.atomic():
                usage, _ = ExternalAPICallUsage.objects.select_for_update().get_or_create(
                    service="google_maps_routes",
                    day=today,
                    defaults={"count": 0},
                )
                if usage.count >= daily_limit:
                    return False

                usage.count += 1
                usage.save(update_fields=["count", "updated_at"])
            return True
        except Exception as e:
            self.log(f"Failed to enforce API usage limits: {e}", level='ERROR')
            return False

    def reserve_external_api_call(self):
        return self._reserve_external_api_call()

    def get_travel_time(self, address1, address2, departure_time=None):
        fallback_time = self._fallback_travel_time()
        api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not api_key:
            self.log(
                f"API key missing for Google Maps Distance Matrix API. Defaulting to {fallback_time} minutes.",
                level='WARNING'
            )
            return fallback_time

        cache_key = f"{address1}|{address2}"
        travel_times = self.load_travel_times()
        cached_time = travel_times.get(cache_key)
        if cached_time and cached_time > 5:
            return cached_time

        if not self._reserve_external_api_call():
            self.log(
                "External API call blocked by configured daily limit. "
                f"Using fallback travel time ({fallback_time} minutes).",
                level='WARNING'
            )
            travel_times[cache_key] = fallback_time
            travel_times[f"{address2}|{address1}"] = fallback_time
            self.save_travel_times(travel_times)
            return fallback_time

        url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
        payload = {
            "origins": [{"waypoint": {"address": address1}}],
            "destinations": [{"waypoint": {"address": address2}}],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE"
        }
        if departure_time:
            central = pytz.timezone('America/Chicago')
            departure_time_utc = central.localize(departure_time).astimezone(pytz.utc)
            payload["departureTime"] = departure_time_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        headers = {
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "duration",
            "Content-Type": "application/json"
        }

        session = requests.Session()
        retry_strategy = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)

        try:
            response = session.post(url, json=payload, headers=headers, timeout=10)
            result = response.json()
            self.log(f"API response for {address1} to {address2}: {result}", level='INFO')
            if response.status_code == 200 and result and "duration" in result[0]:
                duration_str = result[0]["duration"]
                duration_seconds = int(duration_str[:-1]) if duration_str.endswith('s') else int(duration_str)
                travel_time = max(duration_seconds / 60, 5)
                travel_times[cache_key] = travel_time
                travel_times[f"{address2}|{address1}"] = travel_time
                self.save_travel_times(travel_times)
                return travel_time
            else:
                self.log(
                    f"No duration returned for {address1} to {address2}. Defaulting to {fallback_time} minutes.",
                    level='WARNING'
                )
                travel_times[cache_key] = fallback_time
                travel_times[f"{address2}|{address1}"] = fallback_time
                self.save_travel_times(travel_times)
                return fallback_time
        except Exception as e:
            self.log(
                f"Travel time error for {address1} to {address2}: {e}. Defaulting to {fallback_time} minutes.",
                level='ERROR'
            )
            travel_times[cache_key] = fallback_time
            travel_times[f"{address2}|{address1}"] = fallback_time
            self.save_travel_times(travel_times)
            return fallback_time

    def precompute_travel_times(self, work_orders, technicians):
        locations = set(wo.address for wo in work_orders)
        for tech in technicians:
            if tech.home_address:
                locations.add(tech.home_address)
            else:
                self.log(f"No home address for {tech.name}. Using default.", level='ERROR')
                tech.home_address = "600 W Jackson Blvd, Chicago, IL 60661"
                locations.add(tech.home_address)

        travel_time_cache = self.load_travel_times()
        new_entries = False
        for loc1 in locations:
            for loc2 in locations:
                key = f"{loc1}|{loc2}"
                if key not in travel_time_cache or travel_time_cache[key] <= 5:
                    travel_time = self.get_travel_time(loc1, loc2)
                    travel_time_cache[key] = travel_time
                    new_entries = True

        if new_entries:
            self.save_travel_times(travel_time_cache)
        return travel_time_cache

    def parse_availability(self, availability_str):
        if not availability_str or not availability_str.strip():
            return []
        try:
            availability_str = ' '.join(availability_str.split()).lower().replace('am', 'AM').replace('pm', 'PM')
            availability_str = availability_str.replace(' - ', '-').replace('--', '-')
            if ': ' in availability_str:
                date_slots = availability_str.split('; ') if '; ' in availability_str else [availability_str]
                result = []
                for date_slot in date_slots:
                    if not date_slot.strip():
                        continue
                    date_part, slots_part = date_slot.split(': ')
                    date_obj = datetime.strptime(date_part.strip(), '%m/%d/%Y').date()
                    slots = slots_part.split(', ')
                    for slot in slots:
                        if not slot.strip():
                            continue
                        start_str, end_str = slot.split('-')
                        start_time = datetime.strptime(start_str.strip(), '%I%p').time()
                        end_time = datetime.strptime(end_str.strip(), '%I%p').time()
                        result.append((date_obj, start_time, end_time))
                return result
            parts = availability_str.split(' ', 1)
            if len(parts) != 2:
                raise ValueError("Input must contain both time and day parts")
            first_part, second_part = parts
            if '-' in first_part:
                time_part = first_part
                days_part = second_part
            else:
                days_part = first_part
                time_part = second_part
            start_str, end_str = time_part.split('-')
            start_time = datetime.strptime(start_str.strip(), '%I%p').time()
            end_time = datetime.strptime(end_str.strip(), '%I%p').time()
            days = [day.strip() for day in days_part.split(',')]
            day_mapping = {
                'monday': 'Mon', 'tuesday': 'Tue', 'wednesday': 'Wed',
                'thursday': 'Thu', 'friday': 'Fri', 'saturday': 'Sat', 'sunday': 'Sun',
                'mon': 'Mon', 'tue': 'Tue', 'wed': 'Wed', 'thu': 'Thu', 'fri': 'Fri', 'sat': 'Sat', 'sun': 'Sun'
            }
            valid_days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            mapped_days = [day_mapping.get(day, day) for day in days]
            return [(day, start_time, end_time) for day in mapped_days if day in valid_days]
        except ValueError as e:
            self.log(f"Invalid availability: {availability_str} - {e}", level='WARNING')
            return []

    def get_date_for_day_of_week(self, start_date, day_of_week):
        days_of_week = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        target_day_index = days_of_week.index(day_of_week)
        start_day_index = start_date.weekday()
        days_ahead = (target_day_index - start_day_index) % 7
        return start_date + timedelta(days=days_ahead)

    def get_scheduling_days(self, start_date):
        scheduling_days = []
        current_date = start_date
        end_date = start_date + timedelta(days=6)
        while current_date <= end_date:
            scheduling_days.append(current_date)
            current_date += timedelta(days=1)
        return scheduling_days

    def compute_route_travel_time(self, tech, daily_schedule, travel_time_cache):
        if not daily_schedule:
            return 0
        total_travel_time = 0
        sorted_slots = sorted(daily_schedule.items(), key=lambda x: x[0][0])
        prev_loc = tech.home_address
        for (slot_start, slot_end), (wo, start_dt, end_dt, location) in sorted_slots:
            travel_time = travel_time_cache.get(f"{prev_loc}|{location}", 15)
            total_travel_time += travel_time
            prev_loc = location
        travel_time = travel_time_cache.get(f"{prev_loc}|{tech.home_address}", 15)
        total_travel_time += travel_time
        return total_travel_time

    def compute_total_travel_time_for_group(self, assignments):
        total_time = 0
        for tech, assignments_list in assignments.items():
            if not assignments_list:
                continue
            sorted_assignments = sorted(assignments_list, key=lambda a: a.time_slot.start_time)
            prev_location = tech.home_address
            for assignment in sorted_assignments:
                location = assignment.work_order.address
                travel_time = self.get_travel_time(prev_location, location)
                total_time += travel_time
                prev_location = location
            travel_time = self.get_travel_time(prev_location, tech.home_address)
            total_time += travel_time
        return total_time

    def is_balanced_schedule(self, tech_schedules, tech_group, current_date):
        assignment_counts = {}
        for tech in tech_group:
            assignment_counts[tech] = len(tech_schedules[tech][current_date]) if current_date in tech_schedules[tech] else 0
        if not assignment_counts:
            return True
        min_assignments = min(assignment_counts.values())
        max_assignments = max(assignment_counts.values())
        return max_assignments - min_assignments <= 1

    def is_weekly_balanced(self, tech_schedules, tech_group, scheduling_days):
        weekly_counts = {tech: 0 for tech in tech_group}
        for tech in tech_group:
            for date in scheduling_days:
                if date in tech_schedules[tech]:
                    weekly_counts[tech] += len(tech_schedules[tech][date])
        min_counts = min(weekly_counts.values())
        max_counts = max(weekly_counts.values())
        return max_counts - min_counts <= 1

    def optimize_technician_route(self, tech, current_date, tech_schedules, work_orders, travel_time_cache, work_order_slots):
        pass

    def optimize_balanced_day_assignments(self):
        from scheduling.models import WorkOrderAssignment, Technician
        date_activity_assignments = defaultdict(lambda: defaultdict(list))
        date_activity_techs = defaultdict(lambda: defaultdict(list))
        for assignment in WorkOrderAssignment.objects.all():
            date = assignment.date
            activity_type = assignment.work_order.activity_type
            date_activity_assignments[(date, activity_type)].append(assignment)
        all_techs = Technician.objects.all()
        for tech in all_techs:
            for date in set(d for d, _ in date_activity_assignments.keys()):
                date_activity_techs[(date, tech.activity_type)].append(tech)
        for (date, activity_type), assignments in date_activity_assignments.items():
            techs = date_activity_techs[(date, activity_type)]
            if not techs:
                self.log(f"No technicians for {activity_type} on {date}", level='WARNING')
                continue
            num_techs = len(techs)
            num_assignments = len(assignments)
            base_per_tech = num_assignments // num_techs
            remainder = num_assignments % num_techs
            target_counts = [base_per_tech + (1 if i < remainder else 0) for i in range(num_techs)]
            tech_assignments = defaultdict(list)
            for assignment in assignments:
                if assignment.technician:
                    tech_assignments[assignment.technician].append(assignment)
            for tech in techs:
                while len(tech_assignments[tech]) > target_counts[techs.index(tech)]:
                    assignment = tech_assignments[tech].pop()
                    assignment.technician = None
                    assignment.save()
            unassigned = [a for a in assignments if not a.technician]
            tech_index = 0
            for assignment in unassigned:
                while tech_index < len(techs) and len(tech_assignments[techs[tech_index]]) >= target_counts[tech_index]:
                    tech_index = (tech_index + 1) % len(techs)
                assignment.technician = techs[tech_index]
                assignment.save()
                tech_assignments[techs[tech_index]].append(assignment)
            self._optimize_travel_for_balanced_assignment(date, activity_type, tech_assignments)

    def _optimize_travel_for_balanced_assignment(self, date, activity_type, tech_assignments):
        initial_travel_time = self.compute_total_travel_time_for_group(tech_assignments)
        improved = True
        iterations = 0
        max_iterations = 20
        while improved and iterations < max_iterations:
            improved = False
            iterations += 1
            for tech1 in tech_assignments:
                for tech2 in tech_assignments:
                    if tech1 == tech2:
                        continue
                    assignments1 = tech_assignments[tech1]
                    assignments2 = tech_assignments[tech2]
                    if len(assignments1) < len(assignments2):
                        assignments1, assignments2 = assignments2, assignments1
                        tech1, tech2 = tech2, tech1
                    if len(assignments1) - len(assignments2) > 1:
                        continue
                    for a1 in assignments1:
                        if a1.work_order.status in ['scheduled', 'complete']:
                            continue
                        for a2 in assignments2:
                            if a2.work_order.status in ['scheduled', 'complete']:
                                continue
                            temp_assignments = defaultdict(list, {k: v[:] for k, v in tech_assignments.items()})
                            temp_assignments[tech1].remove(a1)
                            temp_assignments[tech2].remove(a2)
                            temp_assignments[tech1].append(a2)
                            temp_assignments[tech2].append(a1)
                            new_travel_time = self.compute_total_travel_time_for_group(temp_assignments)
                            if new_travel_time < initial_travel_time:
                                a1.technician = tech2
                                a2.technician = tech1
                                a1.save()
                                a2.save()
                                tech_assignments[tech1].remove(a1)
                                tech_assignments[tech2].remove(a2)
                                tech_assignments[tech1].append(a2)
                                tech_assignments[tech2].append(a1)
                                initial_travel_time = new_travel_time
                                improved = True
                                self.log(f"Swapped assignments for {activity_type} on {date}: {new_travel_time:.2f} minutes")
        self.log(f"Final travel time for {activity_type} on {date}: {initial_travel_time:.2f} minutes")

    def _compute_day_assignment_cost(self, tech_schedules, tech_group, current_date, travel_time_cache):
        total_travel_time = 0
        for tech in tech_group:
            daily_schedule = tech_schedules[tech][current_date] if current_date in tech_schedules[tech] else {}
            total_travel_time += self.compute_route_travel_time(tech, daily_schedule, travel_time_cache)
        return total_travel_time

    def _reschedule_pending_to_later_date(self, current_date, wo, tech_schedules, technicians, work_order_slots, assigned_work_orders_per_day, tech_total_hours, travel_time_cache):
        start_time = time.time()
        techs_by_activity = defaultdict(list)
        for tech in technicians:
            techs_by_activity[tech.activity_type].append(tech)
        tech_group = techs_by_activity[wo.activity_type]
        if not tech_group:
            self.log(f"No technicians for activity type {wo.activity_type} on {current_date}", level='WARNING')
            return False

        target_slots = [(slot_start, slot_end) for slot_date, slot_start, slot_end in work_order_slots.get(wo, []) if slot_date == current_date]
        if not target_slots:
            self.log(f"No target slots for {wo.ticket_number} on {current_date}", level='DEBUG')
            return False

        # Check if we can schedule the work order to a later date
        later_slots = [(slot_date, s_start, s_end) for slot_date, s_start, s_end in work_order_slots.get(wo, []) if slot_date > current_date]
        if later_slots:
            later_slots.sort(key=lambda x: x[0])
            for new_date, new_slot_start, new_slot_end in later_slots[:2]:
                if time.time() - start_time > 1.0:
                    self.log(f"Rescheduling timeout for {wo.ticket_number}", level='DEBUG')
                    break
                available_techs = [
                    t for t in tech_group
                    if new_date not in tech_schedules[t] or (new_slot_start, new_slot_end) not in tech_schedules[t][new_date]
                ]
                if not available_techs:
                    self.log(f"No technicians available for {wo.ticket_number} on {new_date} at {new_slot_start}-{new_slot_end}", level='DEBUG')
                    continue
                # Prioritize tech with fewest assignments to improve balance
                available_techs.sort(key=lambda t: (
                    len(tech_schedules[t][new_date]) if new_date in tech_schedules[t] else 0,
                    sum(len(tech_schedules[t][d]) for d in tech_schedules[t])  # Weekly total
                ))
                target_tech = available_techs[0]
                temp_schedules = {t: {d: tech_schedules[t][d].copy() for d in tech_schedules[t]} for t in tech_group}
                if new_date not in temp_schedules[target_tech]:
                    temp_schedules[target_tech][new_date] = {}
                new_start_dt = datetime.combine(new_date, new_slot_start)
                new_end_dt = datetime.combine(new_date, new_slot_end)
                temp_schedules[target_tech][new_date][(new_slot_start, new_slot_end)] = (
                    wo, new_start_dt, new_end_dt, wo.address
                )
                # Only proceed if balance is maintained or improved
                if self.is_balanced_schedule(temp_schedules, tech_group, new_date):
                    for t in tech_group:
                        tech_schedules[t].update(temp_schedules[t])
                    assigned_work_orders_per_day[new_date].add(wo)
                    tech_total_hours[target_tech] += 2
                    self.log(f"Scheduled {wo.ticket_number} to {target_tech.name} on {new_date} "
                             f"at {new_slot_start.strftime('%H:%M')}-{new_slot_end.strftime('%H:%M')}")
                    return True

        # Try to reschedule a pending work order to make room
        pending_wos = []
        for tech in tech_group:
            if current_date not in tech_schedules[tech]:
                continue
            for slot in target_slots:
                if slot in tech_schedules[tech][current_date]:
                    current_wo, start_dt, end_dt, location = tech_schedules[tech][current_date][slot]
                    if current_wo.status in ['scheduled', 'complete']:
                        continue
                    later_slots = [(slot_date, s_start, s_end) for slot_date, s_start, s_end in work_order_slots.get(current_wo, []) if slot_date > current_date]
                    if later_slots:
                        pending_wos.append((current_wo, tech, slot[0], slot[1], start_dt, end_dt, location))

        if not pending_wos:
            self.log(f"No pending work orders to reschedule for {wo.ticket_number} on {current_date}", level='DEBUG')
            return False

        max_attempts = min(2, len(pending_wos))
        attempt_count = 0
        timeout = 1.0

        for current_wo, tech, slot_start, slot_end, start_dt, end_dt, location in pending_wos:
            if attempt_count >= max_attempts or time.time() - start_time > timeout:
                self.log(f"Rescheduling timeout or max attempts reached for {wo.ticket_number}", level='DEBUG')
                break
            attempt_count += 1

            later_slots = [(slot_date, s_start, s_end) for slot_date, s_start, s_end in work_order_slots.get(current_wo, []) if slot_date > current_date]
            later_slots.sort(key=lambda x: x[0])
            for new_date, new_slot_start, new_slot_end in later_slots[:2]:
                if time.time() - start_time > timeout:
                    self.log(f"Rescheduling timeout for {wo.ticket_number}", level='DEBUG')
                    break
                available_techs = [
                    t for t in tech_group
                    if new_date not in tech_schedules[t] or (new_slot_start, new_slot_end) not in tech_schedules[t][new_date]
                ]
                if not available_techs:
                    self.log(f"No technicians available for {current_wo.ticket_number} on {new_date} at {new_slot_start}-{new_slot_end}", level='DEBUG')
                    continue
                available_techs.sort(key=lambda t: (
                    len(tech_schedules[t][new_date]) if new_date in tech_schedules[t] else 0,
                    sum(len(tech_schedules[t][d]) for d in tech_schedules[t])
                ))
                target_tech = available_techs[0]
                temp_schedules = {t: {d: tech_schedules[t][d].copy() for d in tech_schedules[t]} for t in tech_group}
                if current_date in temp_schedules[tech]:
                    temp_schedules[tech][current_date].pop((slot_start, slot_end), None)
                if current_date not in temp_schedules[tech]:
                    temp_schedules[tech][current_date] = {}
                if new_date not in temp_schedules[target_tech]:
                    temp_schedules[target_tech][new_date] = {}
                new_start_dt = datetime.combine(new_date, new_slot_start)
                new_end_dt = datetime.combine(new_date, new_slot_end)
                temp_schedules[target_tech][new_date][(new_slot_start, new_slot_end)] = (
                    current_wo, new_start_dt, new_end_dt, current_wo.address
                )
                temp_schedules[tech][current_date][(slot_start, slot_end)] = (
                    wo, start_dt, end_dt, wo.address
                )
                if not self.is_balanced_schedule(temp_schedules, tech_group, current_date) or \
                   not self.is_balanced_schedule(temp_schedules, tech_group, new_date):
                    self.log(f"Rescheduling {current_wo.ticket_number} to {new_date} breaks balance", level='DEBUG')
                    continue
                for t in tech_group:
                    tech_schedules[t].update(temp_schedules[t])
                assigned_work_orders_per_day[current_date].add(wo)
                assigned_work_orders_per_day[new_date].add(current_wo)
                tech_total_hours[tech] += 2
                tech_total_hours[target_tech] += 2
                self.log(f"Moved {current_wo.ticket_number} from {tech.name} on {current_date} to {target_tech.name} on {new_date} "
                         f"at {new_slot_start.strftime('%H:%M')}-{new_slot_end.strftime('%H:%M')} to schedule {wo.ticket_number}")
                return True

        self.log(f"Could not reschedule any work order to accommodate {wo.ticket_number} on {current_date}", level='DEBUG')
        return False

    def _reassign_slots_for_day(self, current_date, unscheduled, tech_schedules, technicians, work_order_slots, assigned_work_orders_per_day, tech_total_hours, travel_time_cache, work_orders):
        missed_work_orders = [
            wo for wo in unscheduled
            if any(slot_date == current_date for slot_date, _, _ in work_order_slots.get(wo, []))
        ]
        if not missed_work_orders:
            return False
        reassignments_made = False
        techs_by_activity = defaultdict(list)
        for tech in technicians:
            techs_by_activity[tech.activity_type].append(tech)

        for wo in missed_work_orders:
            target_slots = [
                (slot_start, slot_end)
                for slot_date, slot_start, slot_end in work_order_slots.get(wo, [])
                if slot_date == current_date
            ]
            tech_group = techs_by_activity[wo.activity_type]
            if not tech_group:
                self.log(f"No technicians available for activity type {wo.activity_type} on {current_date}", level='WARNING')
                continue
            tech_counts = [
                (tech, len(tech_schedules[tech][current_date]) if current_date in tech_schedules[tech] else 0)
                for tech in tech_group
            ]
            tech_counts.sort(key=lambda x: x[1])

            for slot_start, slot_end in target_slots:
                for tech, _ in tech_counts:
                    if current_date in tech_schedules[tech] and (slot_start, slot_end) in tech_schedules[tech][current_date]:
                        continue
                    occupied_techs = [
                        t for t in tech_group
                        if (current_date in tech_schedules[t] and
                            (slot_start, slot_end) in tech_schedules[t][current_date] and
                            t != tech)
                    ]
                    if occupied_techs:
                        occupied_techs.sort(
                            key=lambda t: len(tech_schedules[t][current_date]) if current_date in tech_schedules[t] else 0,
                            reverse=True
                        )
                        for occupied_tech in occupied_techs[:2]:
                            current_wo, start_dt, end_dt, location = tech_schedules[occupied_tech][current_date][(slot_start, slot_end)]
                            if current_wo.status in ['scheduled', 'complete']:
                                continue
                            other_slots = [
                                (s_start, s_end)
                                for s_start, s_end in self.SLOTS
                                if (s_start, s_end) != (slot_start, slot_end) and
                                (current_date, s_start, s_end) in [(sd, ss, se) for sd, ss, se in work_order_slots.get(current_wo, [])] and
                                (current_date not in tech_schedules[occupied_tech] or (s_start, s_end) not in tech_schedules[occupied_tech][current_date])
                            ]
                            if other_slots:
                                current_travel = self.compute_route_travel_time(occupied_tech, tech_schedules[occupied_tech][current_date], travel_time_cache)
                                new_slot_start, new_slot_end = other_slots[0]
                                new_start_dt = datetime.combine(current_date, new_slot_start)
                                new_end_dt = datetime.combine(current_date, new_slot_end)
                                temp_schedule = tech_schedules[occupied_tech][current_date].copy()
                                del temp_schedule[(slot_start, slot_end)]
                                temp_schedule[(new_slot_start, new_slot_end)] = (current_wo, new_start_dt, new_end_dt, current_wo.address)
                                new_travel = self.compute_route_travel_time(occupied_tech, temp_schedule, travel_time_cache)
                                if new_travel <= current_travel * 1.1:
                                    temp_schedules = {t: {d: tech_schedules[t][d].copy() for d in tech_schedules[t]} for t in tech_group}
                                    temp_schedules[occupied_tech][current_date] = temp_schedule
                                    if current_date not in temp_schedules[tech]:
                                        temp_schedules[tech][current_date] = {}
                                    temp_schedules[tech][current_date][(slot_start, slot_end)] = (
                                        wo, start_dt, end_dt, wo.address
                                    )
                                    if self.is_balanced_schedule(temp_schedules, tech_group, current_date):
                                        tech_schedules[occupied_tech][current_date] = temp_schedule
                                        tech_schedules[tech][current_date][(slot_start, slot_end)] = (
                                            wo, start_dt, end_dt, wo.address
                                        )
                                        assigned_work_orders_per_day[current_date].add(wo)
                                        if wo in unscheduled:
                                            unscheduled.remove(wo)
                                        reassignments_made = True
                                        self.log(f"Reassigned {current_wo.ticket_number} to {new_slot_start.strftime('%H:%M')}-{new_slot_end.strftime('%H:%M')} "
                                                 f"for {occupied_tech.name} and scheduled {wo.ticket_number} to {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} "
                                                 f"for {tech.name} on {current_date}")
                                        break
                    else:
                        temp_schedules = {t: {d: tech_schedules[t][d].copy() for d in tech_schedules[t]} for t in tech_group}
                        if current_date not in temp_schedules[tech]:
                            temp_schedules[tech][current_date] = {}
                        start_dt = datetime.combine(current_date, slot_start)
                        end_dt = datetime.combine(current_date, slot_end)
                        temp_schedules[tech][current_date][(slot_start, slot_end)] = (
                            wo, start_dt, end_dt, wo.address
                        )
                        if self.is_balanced_schedule(temp_schedules, tech_group, current_date):
                            tech_schedules[tech][current_date][(slot_start, slot_end)] = (
                                wo, start_dt, end_dt, wo.address
                            )
                            assigned_work_orders_per_day[current_date].add(wo)
                            if wo in unscheduled:
                                unscheduled.remove(wo)
                            reassignments_made = True
                            self.log(f"Scheduled {wo.ticket_number} to {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} "
                                     f"for {tech.name} on {current_date}")
                            break
                if reassignments_made:
                    break
            if reassignments_made:
                break
        return reassignments_made

    def optimize_daily_routes(self, technicians, tech_schedules, work_orders, travel_time_cache, scheduling_days, work_order_slots):
        from scheduling.models import WorkOrderAssignment
        for current_date in scheduling_days:
            techs_by_activity = defaultdict(list)
            for tech in technicians:
                techs_by_activity[tech.activity_type].append(tech)
            for activity_type, tech_group in techs_by_activity.items():
                if len(tech_group) < 2:
                    continue
                assignments = WorkOrderAssignment.objects.filter(
                    date=current_date,
                    work_order__activity_type=activity_type
                )
                if not assignments:
                    continue
                if all(a.work_order.status in ['scheduled', 'complete'] for a in assignments):
                    self.log(f"Skipping optimization for {activity_type} on {current_date}: all assignments are locked", level='DEBUG')
                    continue
                num_techs = len(tech_group)
                num_assignments = sum(1 for a in assignments if a.work_order.status == 'pending')
                base_per_tech = num_assignments // num_techs
                remainder = num_assignments % num_techs
                target_counts = [base_per_tech + (1 if i < remainder else 0) for i in range(num_techs)]

                if self.is_balanced_schedule(tech_schedules, tech_group, current_date):
                    self.log(f"Skipping optimization for {activity_type} on {current_date}: already balanced", level='DEBUG')
                    continue

                best_schedules = {tech: tech_schedules[tech][current_date].copy() if current_date in tech_schedules[tech] else {} for tech in tech_group}
                best_cost = self._compute_day_assignment_cost(tech_schedules, tech_group, current_date, travel_time_cache)
                self.log(f"Initial travel time for {activity_type} on {current_date}: {best_cost:.1f} minutes")

                max_iterations = 5
                iteration = 0
                improved = False

                while iteration < max_iterations:
                    improved = False
                    iteration += 1
                    for slot_start, slot_end in self.SLOTS:
                        assigned_techs = []
                        for tech in tech_group:
                            if current_date in tech_schedules[tech] and (slot_start, slot_end) in tech_schedules[tech][current_date]:
                                wo, start_dt, end_dt, location = tech_schedules[tech][current_date][(slot_start, slot_end)]
                                if wo in work_orders and wo.status not in ['scheduled', 'complete']:
                                    assigned_techs.append((tech, wo, location))
                        if len(assigned_techs) < 2:
                            continue

                        initial_travel_time = sum(self.compute_route_travel_time(tech, tech_schedules[tech][current_date], travel_time_cache) for tech in tech_group)
                        best_travel_time = initial_travel_time
                        best_assignments = {tech: tech_schedules[tech][current_date].copy() if current_date in tech_schedules[tech] else {} for tech in tech_group}

                        for i in range(len(assigned_techs)):
                            for j in range(i + 1, len(assigned_techs)):
                                tech_a, wo_a, loc_a = assigned_techs[i]
                                tech_b, wo_b, loc_b = assigned_techs[j]
                                new_schedule_a = tech_schedules[tech_a][current_date].copy()
                                new_schedule_b = tech_schedules[tech_b][current_date].copy()
                                start_a = datetime.combine(current_date, slot_start)
                                end_a = datetime.combine(current_date, slot_end)
                                start_b = datetime.combine(current_date, slot_start)
                                end_b = datetime.combine(current_date, slot_end)
                                new_schedule_a[(slot_start, slot_end)] = (wo_b, start_a, end_a, loc_b)
                                new_schedule_b[(slot_start, slot_end)] = (wo_a, start_b, end_b, loc_a)
                                temp_schedules = {tech: tech_schedules[tech].copy() for tech in tech_group}
                                temp_schedules[tech_a][current_date] = new_schedule_a
                                temp_schedules[tech_b][current_date] = new_schedule_b
                                if not self.is_balanced_schedule(temp_schedules, tech_group, current_date):
                                    continue
                                new_travel_time = sum(self.compute_route_travel_time(tech, temp_schedules[tech][current_date], travel_time_cache) for tech in tech_group)
                                if new_travel_time < best_travel_time:
                                    tech_schedules[tech_a][current_date] = new_schedule_a
                                    tech_schedules[tech_b][current_date] = new_schedule_b
                                    best_travel_time = new_travel_time
                                    best_assignments = {tech: temp_schedules[tech][current_date].copy() for tech in tech_group}
                                    improved = True
                                    self.log(f"Slot swap for {activity_type} on {current_date} at {slot_start}-{slot_end}: {new_travel_time:.1f} minutes")

                        if improved:
                            for tech in tech_group:
                                if current_date in best_assignments[tech]:
                                    tech_schedules[tech][current_date] = best_assignments[tech]
                            initial_travel_time = best_travel_time
                        else:
                            break
                    if not improved:
                        break

    def schedule(self, work_orders, technicians, start_date, initial_schedules=None):
        if initial_schedules is None:
            tech_schedules = {tech: defaultdict(dict) for tech in technicians}
        else:
            tech_schedules = initial_schedules
        WorkOrderAssignment.objects.all().delete()
        if not work_orders:
            self.log('No pending work orders to schedule.', level='WARNING')
            return technicians, {}
        if not technicians:
            self.log('No technicians available for scheduling.', level='WARNING')
            return technicians, {}

        self.log(f"Received work orders to schedule: {[wo.ticket_number for wo in work_orders]}", level='DEBUG')
        travel_time_cache = self.precompute_travel_times(work_orders, technicians)
        tech_total_hours = {tech: 0 for tech in technicians}
        tech_weekly_counts = {tech: 0 for tech in technicians}
        unscheduled = list(work_orders)
        assigned_work_orders_per_day = defaultdict(set)
        scheduling_days = self.get_scheduling_days(start_date)
        max_days = start_date + timedelta(days=14)
        work_order_slots = {}
        processed_days = set()

        all_work_orders = set(work_orders)
        for tech in technicians:
            for date in scheduling_days:
                if date in tech_schedules[tech]:
                    for _, (wo, _, _, _) in tech_schedules[tech][date].items():
                        all_work_orders.add(wo)
        for wo in all_work_orders:
            availability = self.parse_availability(wo.customer_availability)
            possible_slots = []
            if not availability:
                self.log(f"No availability for {wo.ticket_number}", level='WARNING')
                work_order_slots[wo] = possible_slots
                continue
            if isinstance(availability[0], tuple) and isinstance(availability[0][0], str):
                for day, cust_start, cust_end in availability:
                    possible_date = self.get_date_for_day_of_week(start_date, day)
                    while possible_date <= max_days:
                        if possible_date >= start_date:
                            for slot_start, slot_end in self.SLOTS:
                                if cust_start <= slot_start and cust_end >= slot_end:
                                    possible_slots.append((possible_date, slot_start, slot_end))
                        possible_date += timedelta(days=7)
            else:
                for date_obj, cust_start, cust_end in availability:
                    if date_obj >= start_date and date_obj <= max_days:
                        for slot_start, slot_end in self.SLOTS:
                            if cust_start <= slot_start and cust_end >= slot_end:
                                possible_slots.append((date_obj, slot_start, slot_end))
            work_order_slots[wo] = possible_slots
            self.log(f"Availability for {wo.ticket_number}: {[(d.strftime('%Y-%m-%d'), s.strftime('%H:%M'), e.strftime('%H:%M')) for d, s, e in possible_slots]}", level='DEBUG')

        techs_by_activity = defaultdict(list)
        for tech in technicians:
            techs_by_activity[tech.activity_type].append(tech)

        for current_date in scheduling_days:
            if current_date in processed_days:
                continue
            daily_work_orders = [
                wo for wo in unscheduled
                if any(slot_date == current_date for slot_date, _, _ in work_order_slots.get(wo, []))
            ]
            if not daily_work_orders:
                self.log(f"No work orders available for {current_date}", level='DEBUG')
                processed_days.add(current_date)
                continue

            for activity_type, tech_group in techs_by_activity.items():
                daily_wos = [
                    wo for wo in daily_work_orders
                    if wo.activity_type.lower().strip() == activity_type.lower().strip()
                ]
                if not daily_wos:
                    self.log(f"No work orders for activity type {activity_type} on {current_date}", level='DEBUG')
                    continue

                # Calculate target assignments for balance
                locked_assignments = sum(
                    len([wo for wo, _, _, _ in tech_schedules[tech][current_date].values()
                         if wo.status in ['scheduled', 'complete']])
                    if current_date in tech_schedules[tech] else 0
                    for tech in tech_group
                )
                pending_assignments = sum(
                    len([wo for wo, _, _, _ in tech_schedules[tech][current_date].values()
                         if wo.status == 'pending'])
                    if current_date in tech_schedules[tech] else 0
                    for tech in tech_group
                )
                total_assignments = locked_assignments + pending_assignments + len(daily_wos)
                num_techs = len(tech_group)
                base_per_tech = total_assignments // num_techs
                remainder = total_assignments % num_techs
                target_counts = [base_per_tech + (1 if i < remainder else 0) for i in range(num_techs)]

                techs_sorted = sorted(
                    tech_group,
                    key=lambda t: (
                        len([wo for wo, _, _, _ in tech_schedules[t][current_date].values()
                             if wo.status == 'pending']) if current_date in tech_schedules[t] else 0,
                        tech_weekly_counts[t],
                        tech_total_hours[t]
                    )
                )

                for slot_start, slot_end in self.SLOTS:
                    available_techs = [
                        tech for tech in techs_sorted
                        if (slot_start, slot_end) not in tech_schedules[tech][current_date]
                    ]
                    if not available_techs:
                        self.log(f"No available technicians for slot {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} on {current_date}", level='DEBUG')
                        continue

                    available_techs.sort(
                        key=lambda t: (
                            len(tech_schedules[t][current_date]) if current_date in tech_schedules[t] else 0,
                            tech_weekly_counts[t]
                        )
                    )

                    for tech in available_techs:
                        current_assignments = len(tech_schedules[tech][current_date]) if current_date in tech_schedules[tech] else 0
                        if current_assignments >= max(target_counts):
                            continue

                        previous_slots = [(s_start, s_end) for s_start, s_end in self.SLOTS if s_start < slot_start]
                        current_location = tech.home_address
                        for prev_slot in reversed(previous_slots):
                            if prev_slot in tech_schedules[tech][current_date]:
                                _, _, _, current_location = tech_schedules[tech][current_date][prev_slot]
                                break

                        candidates = [
                            wo for wo in daily_wos
                            if any(
                                slot_date == current_date and slot_start_time == slot_start and slot_end_time == slot_end
                                for slot_date, slot_start_time, slot_end_time in work_order_slots.get(wo, [])
                            )
                            and wo not in assigned_work_orders_per_day[current_date]
                        ]
                        if not candidates:
                            self.log(f"No candidate work orders for slot {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} on {current_date} for {tech.name}", level='DEBUG')
                            continue

                        # Test each candidate to ensure balance
                        best_wo = None
                        best_schedules = None
                        min_imbalance = float('inf')
                        for wo in candidates:
                            temp_schedules = {t: {d: tech_schedules[t][d].copy() for d in tech_schedules[t]} for t in tech_group}
                            if current_date not in temp_schedules[tech]:
                                temp_schedules[tech][current_date] = {}
                            start_dt = datetime.combine(current_date, slot_start)
                            end_dt = datetime.combine(current_date, slot_end)
                            temp_schedules[tech][current_date][(slot_start, slot_end)] = (
                                wo, start_dt, end_dt, wo.address
                            )
                            assignment_counts = {
                                t: len(temp_schedules[t][current_date]) if current_date in temp_schedules[t] else 0
                                for t in tech_group
                            }
                            imbalance = max(assignment_counts.values()) - min(assignment_counts.values())
                            if imbalance <= 1 and imbalance < min_imbalance:
                                min_imbalance = imbalance
                                best_wo = wo
                                best_schedules = temp_schedules

                        if best_wo:
                            for t in tech_group:
                                tech_schedules[t].update(best_schedules[t])
                            tech_total_hours[tech] += 2
                            tech_weekly_counts[tech] += 1
                            assigned_work_orders_per_day[current_date].add(best_wo)
                            if best_wo in unscheduled:
                                unscheduled.remove(best_wo)
                            self.log(f"Assigned {best_wo.ticket_number} to {tech.name} on {current_date} at {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')}")

            missed_work_orders = [
                wo for wo in unscheduled
                if any(slot_date == current_date for slot_date, _, _ in work_order_slots.get(wo, []))
            ]
            max_reschedule_attempts = 1
            for _ in range(max_reschedule_attempts):
                rescheduled = False
                for wo in missed_work_orders[:]:
                    if self._reschedule_pending_to_later_date(
                        current_date, wo, tech_schedules, technicians, work_order_slots,
                        assigned_work_orders_per_day, tech_total_hours, travel_time_cache
                    ):
                        if wo in unscheduled:
                            unscheduled.remove(wo)
                        rescheduled = True
                if not rescheduled:
                    break
                missed_work_orders = [
                    wo for wo in unscheduled
                    if any(slot_date == current_date for slot_date, _, _ in work_order_slots.get(wo, []))
                ]

            if self._reassign_slots_for_day(
                current_date, unscheduled, tech_schedules, technicians,
                work_order_slots, assigned_work_orders_per_day, tech_total_hours,
                travel_time_cache, work_orders
            ):
                missed_work_orders = [
                    wo for wo in unscheduled
                    if any(slot_date == current_date for slot_date, _, _ in work_order_slots.get(wo, []))
                ]

            processed_days.add(current_date)

        # Weekly balancing
        for activity_type, tech_group in techs_by_activity.items():
            max_balance_attempts = 5
            attempt = 0
            while not self.is_weekly_balanced(tech_schedules, tech_group, scheduling_days) and attempt < max_balance_attempts:
                weekly_counts = {tech: 0 for tech in tech_group}
                for tech in tech_group:
                    for date in scheduling_days:
                        if date in tech_schedules[tech]:
                            weekly_counts[tech] += len(tech_schedules[tech][date])
                max_tech = max(weekly_counts, key=weekly_counts.get)
                min_tech = min(weekly_counts, key=weekly_counts.get)
                if weekly_counts[max_tech] - weekly_counts[min_tech] <= 1:
                    break

                for date in scheduling_days:
                    if date not in tech_schedules[max_tech] or not tech_schedules[max_tech][date]:
                        continue
                    for (slot_start, slot_end), (wo, start_dt, end_dt, location) in list(tech_schedules[max_tech][date].items()):
                        if wo.status in ['scheduled', 'complete']:
                            continue
                        if (date not in tech_schedules[min_tech] or 
                            (slot_start, slot_end) not in tech_schedules[min_tech][date]) and \
                           any(slot_date == date and slot_start_time == slot_start and slot_end_time == slot_end
                               for slot_date, slot_start_time, slot_end_time in work_order_slots.get(wo, [])):
                            max_tech_travel = self.compute_route_travel_time(max_tech, tech_schedules[max_tech][date], travel_time_cache)
                            min_tech_travel = self.compute_route_travel_time(min_tech, tech_schedules[min_tech][date] if date in tech_schedules[min_tech] else {}, travel_time_cache)
                            temp_max_schedule = tech_schedules[max_tech][date].copy()
                            temp_min_schedule = tech_schedules[min_tech][date].copy() if date in tech_schedules[min_tech] else {}
                            del temp_max_schedule[(slot_start, slot_end)]
                            temp_min_schedule[(slot_start, slot_end)] = (wo, start_dt, end_dt, location)
                            new_max_travel = self.compute_route_travel_time(max_tech, temp_max_schedule, travel_time_cache)
                            new_min_travel = self.compute_route_travel_time(min_tech, temp_min_schedule, travel_time_cache)
                            temp_schedules = {t: tech_schedules[t].copy() for t in tech_group}
                            temp_schedules[max_tech][date] = temp_max_schedule
                            temp_schedules[min_tech][date] = temp_min_schedule
                            if new_max_travel + new_min_travel <= max_tech_travel + min_tech_travel * 1.1 and \
                               self.is_balanced_schedule(temp_schedules, tech_group, date):
                                tech_schedules[max_tech][date] = temp_max_schedule
                                tech_schedules[min_tech][date] = temp_min_schedule
                                tech_weekly_counts[max_tech] -= 1
                                tech_weekly_counts[min_tech] += 1
                                self.log(f"Rebalanced {wo.ticket_number} from {max_tech.name} to {min_tech.name} on {date}")
                                break
                    if weekly_counts[max_tech] - weekly_counts[min_tech] <= 1:
                        break
                attempt += 1

        self.optimize_balanced_day_assignments()
        self.optimize_daily_routes(technicians, tech_schedules, work_orders, travel_time_cache, scheduling_days, work_order_slots)

        if unscheduled:
            extended_days = []
            current_date = scheduling_days[-1] + timedelta(days=1)
            while current_date <= max_days and unscheduled:
                extended_days.append(current_date)
                current_date += timedelta(days=1)
            scheduling_days.extend(extended_days)
            remaining_assignments = []
            for wo in unscheduled:
                possible_slots = work_order_slots.get(wo, [])
                for slot_date, slot_start, slot_end in possible_slots:
                    if slot_date in scheduling_days:
                        remaining_assignments.append((wo, slot_date, slot_start, slot_end))
            remaining_assignments.sort(key=lambda x: (x[1], x[2]))
            for wo, slot_date, slot_start, slot_end in remaining_assignments:
                available_techs = [
                    tech for tech in technicians
                    if (slot_start, slot_end) not in tech_schedules[tech][slot_date]
                    and wo.activity_type.lower().strip() == tech.activity_type.lower().strip()
                ]
                if not available_techs:
                    self.log(f"No available technicians for {wo.ticket_number} on {slot_date} at {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')}", level='DEBUG')
                    continue
                # Prioritize tech with fewest assignments
                available_techs.sort(key=lambda t: (
                    len(tech_schedules[t][slot_date]) if slot_date in tech_schedules[t] else 0,
                    tech_weekly_counts[t]
                ))
                best_tech = available_techs[0]
                temp_schedules = {t: {d: tech_schedules[t][d].copy() for d in tech_schedules[t]} for t in techs_by_activity[wo.activity_type]}
                if slot_date not in temp_schedules[best_tech]:
                    temp_schedules[best_tech][slot_date] = {}
                start_dt = datetime.combine(slot_date, slot_start)
                end_dt = datetime.combine(slot_date, slot_end)
                temp_schedules[best_tech][slot_date][(slot_start, slot_end)] = (
                    wo, start_dt, end_dt, wo.address
                )
                if self.is_balanced_schedule(temp_schedules, techs_by_activity[wo.activity_type], slot_date):
                    tech_schedules[best_tech][slot_date][(slot_start, slot_end)] = (
                        wo, start_dt, end_dt, wo.address
                    )
                    tech_total_hours[best_tech] += 2
                    tech_weekly_counts[best_tech] += 1
                    assigned_work_orders_per_day[slot_date].add(wo)
                    if wo in unscheduled:
                        unscheduled.remove(wo)
                    self.log(f"Assigned {wo.ticket_number} to {best_tech.name} on {slot_date} at {slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')}")

        for tech in technicians:
            tech.schedule = []
            for date in tech_schedules[tech]:
                for (slot_start, slot_end), assignment in tech_schedules[tech][date].items():
                    tech.schedule.append(assignment)
            tech.schedule.sort(key=lambda x: (x[1].date(), x[1].time()))
            tech.daily_hours = defaultdict(float)
            for _, start, _, _ in tech.schedule:
                date_str = start.date().strftime('%Y-%m-%d')
                tech.daily_hours[date_str] += 2

        if unscheduled:
            self.log("Unscheduled work orders:", level='WARNING')
            for wo in unscheduled:
                self.log(f"  - {wo.ticket_number}")
        return technicians, travel_time_cache

    def print_schedules(self, technicians, travel_time_cache):
        self.log("\n=== Technician Schedules ===")
        for tech in technicians:
            self.log(f"\nTechnician: {tech.name} (Activity Type: {tech.activity_type})")
            if not tech.schedule:
                self.log("  No assignments.")
                continue
            assignments_by_day = defaultdict(list)
            for assignment in tech.schedule:
                date = assignment[1].date()
                assignments_by_day[date].append(assignment)
            for date in sorted(assignments_by_day):
                self.log(f"  {date.strftime('%Y-%m-%d (%a)')}: Total Hours = {tech.daily_hours[date]:.1f}")
                daily_assignments = sorted(assignments_by_day[date], key=lambda x: x[1])
                for i, (wo, start, end, location) in enumerate(daily_assignments):
                    if i + 1 < len(daily_assignments):
                        next_loc = daily_assignments[i + 1][3]
                    else:
                        next_loc = tech.home_address
                    travel_time = travel_time_cache.get(f"{location}|{next_loc}", 15)
                    self.log(
                        f"    - {wo.ticket_number}: {start.strftime('%H:%M')} - {end.strftime('%H:%M')} "
                        f"({location}) (Travel to next: {travel_time:.1f} min)"
                    )
