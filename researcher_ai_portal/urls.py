from django.http import HttpResponse
from django.urls import include, path
from django.contrib.auth.views import LogoutView

from researcher_ai_portal_app.views import (
    dashboard,
    delete_job,
    figure_image_proxy,
    home,
    job_progress,
    job_status,
    parse_progress,
    start_parse,
    workflow_step,
)


def healthz(_: object) -> HttpResponse:
    return HttpResponse('ok', content_type='text/plain')


urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('', home, name='home'),
    path('parse/start/', start_parse, name='start_parse'),
    path('jobs/<str:job_id>/', job_progress, name='job_progress'),
    path('jobs/<str:job_id>/progress/', parse_progress, name='parse_progress'),
    path('jobs/<str:job_id>/workflow/<str:step>/', workflow_step, name='workflow_step'),
    path('jobs/<str:job_id>/status/', job_status, name='job_status'),
    path('jobs/<str:job_id>/figure-image/', figure_image_proxy, name='figure_image_proxy'),
    path('jobs/<str:job_id>/dashboard/', dashboard, name='dashboard'),
    path('jobs/<str:job_id>/delete/', delete_job, name='delete_job'),
    path('django_plotly_dash/', include('django_plotly_dash.urls')),
    path('', include('social_django.urls', namespace='social')),
]
