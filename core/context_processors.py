from .models import Alert, Profile


def shell(request):
    """Values the app shell needs on every signed-in page."""
    user = request.user
    if not user.is_authenticated:
        return {}
    profile, _ = Profile.objects.get_or_create(user=user)
    return {
        "profile": profile,
        "alerts": Alert.objects.filter(user=user, read_at__isnull=True)[:4],
    }
