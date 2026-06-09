from rest_framework import serializers

class LoginUserSerializer(serializers.Serializer):
    """
    Serializer for logging in a user.

    This serializer validates user credentials and returns an authentication token.
    """

    username = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, required=True)

    def validate(self, data):
        """
        Validate the user credentials and return an authentication token.

        Args:
            data (dict): A dictionary containing 'username' and 'password'.

        Returns:
            dict: A dictionary containing the authentication token.
        """
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            raise serializers.ValidationError("Both username and password are required.")

        # Assuming `authenticate` is a function that checks credentials
        from django.contrib.auth import authenticate
        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")

        data['user'] = user
        return data