from __future__ import annotations

import hashlib
import json
import uuid

from django.conf import settings
from django.db import models


class WorkflowJob(models.Model):
    """Durable workflow job record replacing the in-memory job store."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflow_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    source = models.TextField()
    source_type = models.CharField(max_length=16)
    input_display = models.CharField(max_length=255)
    llm_model = models.CharField(max_length=64)

    status = models.CharField(max_length=16, default="queued")
    progress = models.IntegerField(default=0)
    stage = models.CharField(max_length=255, default="Queued")
    current_step = models.CharField(max_length=32, default="paper")
    error = models.TextField(blank=True, default="")

    figure_parse_total = models.IntegerField(default=0)
    figure_parse_current = models.IntegerField(default=0)
    supplementary_figure_ids = models.JSONField(default=list)
    parse_logs = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]


class ComponentSnapshot(models.Model):
    """Stored workflow component JSON and per-step status metadata."""

    job = models.ForeignKey(WorkflowJob, on_delete=models.CASCADE, related_name="components")
    step = models.CharField(max_length=32)
    payload = models.JSONField(default=dict)
    payload_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, default="missing")
    missing_fields = models.JSONField(default=list)
    source = models.CharField(max_length=32, default="parsed")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("job", "step")]

    def save(self, *args, **kwargs):
        self.payload_hash = hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        super().save(*args, **kwargs)


class PaperCache(models.Model):
    """Paper-level parse cache keyed by canonical id (pmid/doi/url)."""

    canonical_id = models.CharField(max_length=64, unique=True, db_index=True)
    paper_json = models.JSONField(default=dict)
    figures_json = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    llm_model = models.CharField(max_length=64)

    class Meta:
        indexes = [models.Index(fields=["canonical_id"])]
