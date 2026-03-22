from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from datetime import datetime, time, timedelta

from core.models import WorkOrder
from scheduling.models import Technician, ScheduleEntry


class Command(BaseCommand):
    help = (
        "Create demo technicians and work orders for local testing "
        "(50 total: 25 scheduled + 25 submitted)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing technicians, schedule entries, and work orders before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            ScheduleEntry.objects.all().delete()
            WorkOrder.objects.all().delete()
            Technician.objects.all().delete()
            self.stdout.write(self.style.WARNING("Existing scheduler data cleared."))

        technician_specs = [
            ("Tech PM 1", "PM", "864 Western Avenue, Glen Ellyn, IL 60137"),
            ("Tech Service 1", "Service", "8821 Mansfield Ave, Morton Grove, IL 60053"),
            ("Tech Service 2", "Service", "530 Calhoun Ave, Calumet City, IL 60409"),
            ("Tech Ice 1", "Ice", "6343 W 90th Pl, Oak Lawn, IL 60453"),
            ("Tech Install 1", "Installation", "600 W Jackson Blvd, Chicago, IL 60661"),
        ]
        technicians = []
        for name, activity_type, home_address in technician_specs:
            tech, _ = Technician.objects.update_or_create(
                name=name,
                defaults={
                    "activity_type": activity_type,
                    "home_address": home_address,
                    "working_start_time": "08:00",
                    "working_end_time": "16:00",
                    "work_days": "Monday,Tuesday,Wednesday,Thursday,Friday",
                },
            )
            technicians.append(tech)

        addresses = [
            "150 N Michigan Ave, Chicago, IL 60601",
            "875 N Michigan Ave, Chicago, IL 60611",
            "233 S Wacker Dr, Chicago, IL 60606",
            "1060 W Addison St, Chicago, IL 60613",
            "540 N Michigan Ave, Chicago, IL 60611",
            "330 N Wabash Ave, Chicago, IL 60611",
            "8750 W Bryn Mawr Ave, Chicago, IL 60631",
            "9700 W Higgins Rd, Rosemont, IL 60018",
            "1234 N Halsted St, Chicago, IL 60642",
            "500 W Madison St, Chicago, IL 60661",
            "401 N Wabash Ave, Chicago, IL 60611",
            "1200 S Lake Shore Dr, Chicago, IL 60605",
            "711 S Dearborn St, Chicago, IL 60605",
            "655 W Irving Park Rd, Chicago, IL 60613",
            "1000 E 111th St, Chicago, IL 60628",
            "1900 W Lawrence Ave, Chicago, IL 60640",
            "1450 N Halsted St, Chicago, IL 60642",
            "820 W Jackson Blvd, Chicago, IL 60607",
            "2600 S California Ave, Chicago, IL 60608",
            "3555 N Broadway, Chicago, IL 60657",
            "200 E Randolph St, Chicago, IL 60601",
            "1050 N Rush St, Chicago, IL 60611",
            "3200 N Ashland Ave, Chicago, IL 60657",
            "700 E Grand Ave, Chicago, IL 60611",
            "50 W Washington St, Chicago, IL 60602",
            "910 W Van Buren St, Chicago, IL 60607",
            "1001 W North Ave, Chicago, IL 60642",
            "4300 N Lincoln Ave, Chicago, IL 60618",
            "1801 W Cermak Rd, Chicago, IL 60608",
            "3350 S Halsted St, Chicago, IL 60608",
            "2100 N Damen Ave, Chicago, IL 60647",
            "5700 N Clark St, Chicago, IL 60660",
            "1400 E 53rd St, Chicago, IL 60615",
            "6700 S Pulaski Rd, Chicago, IL 60629",
            "9900 S Western Ave, Chicago, IL 60643",
            "2250 N Lincoln Ave, Chicago, IL 60614",
            "1717 N Sheffield Ave, Chicago, IL 60614",
            "300 S Riverside Plaza, Chicago, IL 60606",
            "444 N Michigan Ave, Chicago, IL 60611",
            "2000 W Diversey Ave, Chicago, IL 60647",
            "3500 W Fullerton Ave, Chicago, IL 60647",
            "725 W Roosevelt Rd, Chicago, IL 60608",
            "2400 E 79th St, Chicago, IL 60649",
            "118 N Clinton St, Chicago, IL 60661",
            "2601 W 47th St, Chicago, IL 60632",
            "1313 E 60th St, Chicago, IL 60637",
            "7600 S Cottage Grove Ave, Chicago, IL 60619",
            "2800 N Milwaukee Ave, Chicago, IL 60618",
            "4025 W 63rd St, Chicago, IL 60629",
            "5420 N Kedzie Ave, Chicago, IL 60625",
        ]

        activity_cycle = ["PM", "Service", "Service", "Ice", "Installation"]
        slot_times = [
            (time(8, 0), time(10, 0), "8am-10am"),
            (time(10, 0), time(12, 0), "10am-12pm"),
            (time(12, 0), time(14, 0), "12pm-2pm"),
            (time(14, 0), time(16, 0), "2pm-4pm"),
        ]

        today = timezone.localdate()
        days_since_sunday = (today.weekday() + 1) % 7
        week_start_sunday = today - timedelta(days=days_since_sunday)
        week_start = week_start_sunday + timedelta(days=1)

        created = 0
        updated = 0
        scheduled_count = 0
        submitted_count = 0
        ticket_number = 1001
        address_idx = 0

        for day_offset in range(5):
            schedule_date = week_start + timedelta(days=day_offset)
            date_label = f"{schedule_date.month}/{schedule_date.day}/{schedule_date.year}"

            for tech_index, tech in enumerate(technicians):
                slot_start, slot_end, slot_label = slot_times[tech_index % len(slot_times)]
                current_ticket = f"WO-{ticket_number}"
                address = addresses[address_idx % len(addresses)]
                address_idx += 1
                start_dt = timezone.make_aware(datetime.combine(schedule_date, slot_start))
                end_dt = timezone.make_aware(datetime.combine(schedule_date, slot_end))

                wo, was_created = WorkOrder.objects.update_or_create(
                    ticket_number=current_ticket,
                    defaults={
                        "activity_type": tech.activity_type,
                        "address": address,
                        "customer_availability": f"{date_label}: {slot_label}",
                        "site_name": f"Demo Site {current_ticket}",
                        "notes": "Demo seeded: pre-scheduled work order",
                        "status": "scheduled",
                        "scheduled_at": start_dt,
                        "completed_at": None,
                    },
                )

                if was_created:
                    created += 1
                else:
                    updated += 1

                ScheduleEntry.objects.filter(work_order=wo).delete()
                ScheduleEntry.objects.create(
                    technician=tech,
                    work_order=wo,
                    start_time=start_dt,
                    end_time=end_dt,
                    location=address,
                    travel_to_next=15,
                )
                scheduled_count += 1
                ticket_number += 1

        for unscheduled_idx in range(25):
            current_ticket = f"WO-{ticket_number}"
            address = addresses[address_idx % len(addresses)]
            address_idx += 1
            activity_type = activity_cycle[unscheduled_idx % len(activity_cycle)]
            avail_date = week_start + timedelta(days=unscheduled_idx % 5)
            date_label = f"{avail_date.month}/{avail_date.day}/{avail_date.year}"
            slot_label = slot_times[unscheduled_idx % len(slot_times)][2]

            wo, was_created = WorkOrder.objects.update_or_create(
                ticket_number=current_ticket,
                defaults={
                    "activity_type": activity_type,
                    "address": address,
                    "customer_availability": f"{date_label}: {slot_label}",
                    "site_name": f"Demo Site {current_ticket}",
                    "notes": "Demo seeded: submitted and unscheduled work order",
                    "status": "submitted",
                    "scheduled_at": None,
                    "completed_at": None,
                },
            )

            if was_created:
                created += 1
            else:
                updated += 1

            ScheduleEntry.objects.filter(work_order=wo).delete()
            submitted_count += 1
            ticket_number += 1

        total = scheduled_count + submitted_count
        self.stdout.write(
            self.style.SUCCESS(
                "Demo data ready. "
                f"Total work orders: {total} "
                f"(scheduled: {scheduled_count}, submitted: {submitted_count}). "
                f"Created: {created}, updated: {updated}."
            )
        )
