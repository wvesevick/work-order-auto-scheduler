# urls.py
from django.urls import path
from . import views
from .views import (
    ScheduleAPIView, schedule_view, WorkOrdersAPIView, submit_selected_work_orders, work_orders_view, add_work_order,
    create_work_order, upload_csv_work_orders, update_work_order_status,
    schedule_all_work_orders, schedule_selected_work_orders, schedule_api_view, technicians_view, route_preview
)
from django.shortcuts import redirect

def redirect_to_schedule(request):
    return redirect('schedule')

urlpatterns = [
    path('', redirect_to_schedule, name='root'),
    path('schedule/', schedule_view, name='schedule'),
    path('api/schedule-old/', ScheduleAPIView.as_view(), name='schedule_api_old'),
    path('api/schedule/', schedule_api_view, name='schedule_api'),
    path('api/work-orders/', WorkOrdersAPIView.as_view(), name='work_orders_api'),
    path('work-orders/', work_orders_view, name='work_orders'),
    path('technicians/', technicians_view, name='technicians'),
    path('api/technicians/', views.TechniciansAPIView.as_view(), name='technicians_api'),  # New endpoint
    path('api/update-technician/', views.update_technician, name='update_technician'),  # New endpoint
    path('api/add-work-order/', add_work_order, name='add_work_order'),
    path('api/create-work-order/', create_work_order, name='create_work_order'),
    path('api/upload-csv-work-orders/', upload_csv_work_orders, name='upload_csv_work_orders'),
    path('api/update-work-order-status/', update_work_order_status, name='update_work_order_status'),
    path('api/schedule-all/', schedule_all_work_orders, name='schedule_all_work_orders'),
    path('api/schedule-selected/', schedule_selected_work_orders, name='schedule_selected_work_orders'),
    path('api/submit-selected/', submit_selected_work_orders, name='submit_selected_work_orders'),
    path('api/route-preview/', route_preview, name='route_preview'),
    path('api/get-travel-time/', views.get_travel_time, name='get_travel_time'),
    path('api/add-technician/', views.add_technician, name='add_technician'),
    path('api/assign-work-order/', views.assign_work_order, name='assign_work_order'),
]
