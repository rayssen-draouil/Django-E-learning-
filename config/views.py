from django.shortcuts import render


def home(request):
    """Render the public landing page."""
    return render(request, "frontoffice/index.html")
