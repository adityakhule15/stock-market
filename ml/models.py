from django.db import models


class ModelVersion(models.Model):
    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Candidate"
        CHAMPION = "champion", "Champion"
        RETIRED = "retired", "Retired"

    name = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    algo = models.CharField(max_length=30)
    params = models.JSONField(default=dict)
    train_start = models.DateField(null=True, blank=True)
    train_end = models.DateField(null=True, blank=True)
    metrics = models.JSONField(default=dict)
    artifact_path = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CANDIDATE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("name", "version")
        indexes = [models.Index(fields=["name", "status"])]

    def __str__(self) -> str:
        return f"{self.name}_v{self.version} ({self.status})"


class DriftReport(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    model_name = models.CharField(max_length=40)
    psi_max = models.FloatField(default=0.0)
    details = models.JSONField(default=dict)
    triggered_retrain = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["-created_at"])]
