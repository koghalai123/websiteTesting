from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('audio/', views.loadMainPage, name='audioInputPage'),
    path('devices/', views.device_control, name='device_control'),
    path('api/device-control/', views.device_control, name='device_control_api'),
    path('api/device-states/', views.get_device_states, name='device_states_api'),
    path('api/device-config/', views.get_device_config, name='device_config_api'),
    path('api/add-device/', views.add_device, name='add_device_api'),
]