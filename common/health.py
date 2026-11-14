"""
`/healthz/` for the load balancer and the container (G60).

It runs before every other middleware: the load balancer's check comes to the task's private IP, which is not in
ALLOWED_HOSTS, over plain HTTP, which SECURE_SSL_REDIRECT would redirect. It only says the process is serving
requests, never touches the database (a database outage must not make ECS replace every task in a loop).
"""

from django.http import HttpResponse, HttpResponseNotAllowed

PATH = '/healthz/'


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info != PATH:
            return self.get_response(request)
        if request.method not in ('GET', 'HEAD'):
            return HttpResponseNotAllowed(['GET', 'HEAD'])
        return HttpResponse(b'ok', content_type='text/plain')
