from django.shortcuts import render


def home(request):
    """Render the public landing page."""
    return render(request, "frontoffice/index.html")


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)
