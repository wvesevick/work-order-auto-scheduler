# scheduling/migrations/0006_remove_technician_skills_technician_activity_type.py
from django.db import migrations, models

def migrate_skills_to_activity_type(apps, schema_editor):
    Technician = apps.get_model('scheduling', 'Technician')
    for tech in Technician.objects.all():
        if tech.skills:
            skills = tech.skills.split(',')
            # Map the first skill to a valid activity_type choice
            first_skill = skills[0].strip().capitalize()
            valid_choices = ['PM', 'Service', 'Ice', 'Installation']
            tech.activity_type = first_skill if first_skill in valid_choices else 'Service'
        else:
            tech.activity_type = 'Service'
        tech.save()

def reverse_migration(apps, schema_editor):
    # Optional: Define how to reverse the migration (e.g., set skills back)
    Technician = apps.get_model('scheduling', 'Technician')
    for tech in Technician.objects.all():
        tech.skills = tech.activity_type.lower()
        tech.save()

class Migration(migrations.Migration):
    dependencies = [
        ('scheduling', '0005_technician_work_days'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='Technician',
            name='skills',
        ),
        migrations.AddField(
            model_name='Technician',
            name='activity_type',
            field=models.CharField(
                choices=[
                    ('PM', 'PM'),
                    ('Service', 'Service'),
                    ('Ice', 'Ice'),
                    ('Installation', 'Installation'),
                ],
                default='Service',
                max_length=50,
            ),
        ),
        migrations.RunPython(
            migrate_skills_to_activity_type,
            reverse_migration,
        ),
    ]