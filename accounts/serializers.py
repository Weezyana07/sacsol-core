# accounts/serializers.py
from typing import Any
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, AbstractUser
from rest_framework import serializers
from django.contrib.auth import password_validation

UserModel = get_user_model()

# Only assignable roles (superadmin is inferred, not creatable)
ROLE_CHOICES = (
    ("staff", "staff"),
    ("manager", "manager"),
)


def _role_to_group_name(role: str) -> str:
    """Map API role to canonical Group name."""
    return role.capitalize()  # "staff" -> "Staff", "manager" -> "Manager"


def _infer_role_from_groups(user: AbstractUser) -> str:
    """
    Return a display role for the user.
    - superuser => "superadmin"
    - in Manager group => "manager"
    - in Staff group => "staff"
    - fallback => "staff"
    """
    if getattr(user, "is_superuser", False):
        return "superadmin"
    if user.groups.filter(name__iexact="Manager").exists():
        return "manager"
    if user.groups.filter(name__iexact="Staff").exists():
        return "staff"
    return "staff"


class UserBaseSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "is_active",
            "date_joined",
        )
        read_only_fields = ("id", "role", "date_joined")

    def get_role(self, obj: AbstractUser) -> str:
        return _infer_role_from_groups(obj)


class UserCreateSerializer(serializers.ModelSerializer):
    # Role is only accepted on write
    role = serializers.ChoiceField(choices=ROLE_CHOICES, write_only=True)
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = UserModel
        fields = ("id", "username", "email", "first_name", "last_name", "role", "password")

    def create(self, validated_data: dict[str, Any]) -> AbstractUser:
        role = validated_data.pop("role")
        raw_pwd = validated_data.pop("password")

        user: AbstractUser = UserModel.objects.create(**validated_data)
        user.set_password(raw_pwd)

        # Staff vs Manager flags
        user.is_staff = (role == "manager")
        user.save()

        group, _ = Group.objects.get_or_create(name=_role_to_group_name(role))
        user.groups.add(group)
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=ROLE_CHOICES, required=False)

    class Meta:
        model = UserModel
        fields = ("email", "first_name", "last_name", "is_active", "role")

    def update(self, instance: AbstractUser, validated_data: dict[str, Any]) -> AbstractUser:
        role = validated_data.pop("role", None)

        # Role of a superuser cannot be changed through this serializer
        if getattr(instance, "is_superuser", False) and role is not None:
            raise serializers.ValidationError({"role": "Cannot change role of a superuser."})

        # Apply base fields
        for k, v in validated_data.items():
            setattr(instance, k, v)

        if role is not None:
            instance.is_staff = (role == "manager")

            # Normalize groups (case-insensitive find or create)
            staff_g, _ = Group.objects.get_or_create(name="Staff")
            mgr_g, _ = Group.objects.get_or_create(name="Manager")

            instance.groups.remove(staff_g, mgr_g)
            instance.groups.add(mgr_g if role == "manager" else staff_g)

        instance.save()
        return instance


class MeSerializer(UserBaseSerializer):
    """Read-only representation of the current user."""


class SetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, min_length=6)


class ChangeOwnPasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            raise serializers.ValidationError({"detail": "Authentication required."})

        curr = attrs.get("current_password") or ""
        if not user.check_password(curr):
            raise serializers.ValidationError({"current_password": "Current password is incorrect."})

        # Apply Django’s password validators (AUTH_PASSWORD_VALIDATORS)
        new = attrs.get("new_password") or ""
        password_validation.validate_password(new, user)
        return attrs