from rest_framework import serializers


class HmrcStatusSerializer(serializers.Serializer):
    environment = serializers.ChoiceField(choices=['off', 'sandbox', 'production'])
    connected = serializers.BooleanField()
    vrn = serializers.CharField(allow_null=True)
    connected_at = serializers.DateTimeField(allow_null=True)


class AuthorizeUrlSerializer(serializers.Serializer):
    authorize_url = serializers.URLField()
