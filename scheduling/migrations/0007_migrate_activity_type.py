from django.db import migrations

def set_activity_type(apps, schema_editor):
    Technician = apps.get_model('scheduling', 'Technician')
    for tech in Technician.objects.all():
        # Since skills is already removed, set a default or use existing activity_type
        if not tech.activity_type:
            tech.activity_type = 'Service'
        tech.save()

class Migration(migrations.Migration):
    dependencies = [
        ('scheduling', '0006_remove_technician_skills_technician_activity_type'),
    ]

    operations = [
        migrations.RunPython(set_activity_type),
    ]