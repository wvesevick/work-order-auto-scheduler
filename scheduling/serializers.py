# scheduling/serializers.py
from rest_framework import serializers
from core.models import WorkOrder

class WorkOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkOrder
        fields = ['ticket_number', 'activity_type', 'address', 'customer_availability']

class ScheduleEntrySerializer(serializers.Serializer):
    work_order = WorkOrderSerializer()
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    location = serializers.CharField()
    travel_to_next = serializers.FloatField()

    def to_representation(self, instance):
        work_order, start_time, end_time, location, travel_to_next = instance
        return {
            'work_order': WorkOrderSerializer(work_order).data,
            'start_time': start_time,
            'end_time': end_time,
            'location': location,
            'travel_to_next': travel_to_next
        }

class TechnicianScheduleSerializer(serializers.Serializer):
    technician = serializers.CharField(source='name')
    home_address = serializers.CharField()  # Add home_address field
    schedule = ScheduleEntrySerializer(many=True)
    daily_hours = serializers.DictField(child=serializers.FloatField())

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['home_address'] = getattr(instance, 'home_address', 'Unknown Address')
        return representation
