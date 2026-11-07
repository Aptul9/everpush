from django.db import models


class Item(models.Model):
    text = models.TextField(blank=True)
    file_name = models.CharField(max_length=240, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    stored_name = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RateLimit(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
