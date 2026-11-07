from django.urls import path
from push import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("healthz/", views.health, name="health"),
    path("api/items/", views.items, name="items"),
    path("api/items/<int:item_id>/delete/", views.delete, name="delete"),
    path("api/items/<int:item_id>/file/", views.file, name="file"),
]
