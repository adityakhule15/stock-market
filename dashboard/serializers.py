from rest_framework import serializers

from ml.models import DriftReport, ModelVersion
from portfolio.models import EquitySnapshot, Position
from signals.models import Signal


class SignalSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source="instrument.symbol", read_only=True)
    model_version_label = serializers.SerializerMethodField()
    entry = serializers.SerializerMethodField()
    stop_loss = serializers.SerializerMethodField()
    targets = serializers.SerializerMethodField()
    risk_gate = serializers.JSONField(source="gate")

    class Meta:
        model = Signal
        fields = [
            "id",
            "symbol",
            "side",
            "strategy",
            "product",
            "entry",
            "stop_loss",
            "targets",
            "qty",
            "capital_used",
            "max_loss",
            "rr_net",
            "probability",
            "time_machine",
            "risk_gate",
            "model_version_label",
            "data_as_of",
            "data_source",
            "data_age_min",
            "valid_until",
            "reasons",
            "status",
            "mode",
            "skip_reason",
        ]

    def get_model_version_label(self, obj):
        if obj.model_version_id:
            return f"{obj.model_version.name}_v{obj.model_version.version}"
        return None

    def get_entry(self, obj):
        return {
            "trigger": float(obj.entry_trigger),
            "limit": float(obj.entry_limit),
            "skip_if_live_price_above": float(obj.chase_limit),
        }

    def get_stop_loss(self, obj):
        return {"trigger": float(obj.sl_trigger), "limit": float(obj.sl_limit)}

    def get_targets(self, obj):
        return [float(obj.target1), float(obj.target2)]


class PositionSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source="instrument.symbol", read_only=True)

    class Meta:
        model = Position
        fields = "__all__"


class ModelVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelVersion
        fields = "__all__"


class DriftReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = DriftReport
        fields = "__all__"


class EquitySnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquitySnapshot
        fields = "__all__"
