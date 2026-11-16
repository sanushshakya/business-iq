from rest_framework import serializers


class HmrcStatusSerializer(serializers.Serializer):
    environment = serializers.ChoiceField(choices=['off', 'sandbox', 'production'])
    connected = serializers.BooleanField()
    vrn = serializers.CharField(allow_null=True)
    connected_at = serializers.DateTimeField(allow_null=True)


class AuthorizeUrlSerializer(serializers.Serializer):
    authorize_url = serializers.URLField()


class FraudClientSerializer(serializers.Serializer):
    """Every call that reaches HMRC carries the browser's own details for the fraud-prevention headers."""

    client = serializers.DictField()

    def validate_client(self, value):
        from .fraud import ClientInfoSerializer

        info = ClientInfoSerializer(data=value)
        info.is_valid(raise_exception=True)
        return info.validated_data


class ObligationsRequestSerializer(FraudClientSerializer):
    status = serializers.ChoiceField(choices=['O', 'F'], required=False)


class ObligationSerializer(serializers.Serializer):
    periodKey = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    due = serializers.DateField()
    status = serializers.CharField()
    received = serializers.DateField(required=False)
    filed_in_iq = serializers.BooleanField()


class SubmitRequestSerializer(FraudClientSerializer):
    period_key = serializers.CharField(min_length=4, max_length=4)
    start = serializers.DateField(input_formats=['%Y-%m-%d'])
    end = serializers.DateField(input_formats=['%Y-%m-%d'])
    declaration = serializers.BooleanField()

    def validate_declaration(self, value):
        if not value:
            raise serializers.ValidationError('Confirm the declaration: the information is true and complete.')
        return value


class HmrcSubmissionSerializer(serializers.ModelSerializer):
    vat_return = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        from accounting.models import HmrcSubmission

        model = HmrcSubmission
        fields = ['id', 'period_key', 'period_start', 'period_end', 'box1', 'box2', 'box3', 'box4', 'box5', 'box6',
                  'box7', 'box8', 'box9', 'net_direction', 'status', 'submitted_by', 'submitted_at',
                  'processing_date', 'form_bundle_number', 'payment_indicator', 'charge_ref_number', 'error_code',
                  'error_message', 'vat_return']
        read_only_fields = fields


class HmrcErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    hmrc_code = serializers.CharField()
    message = serializers.CharField()
