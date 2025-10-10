# accounts/serializer.py
from typing import Any
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, AbstractUser
from rest_framework import serializers

UserModel = get_user_model()

ROLE_CHOICES = (
    ("staff", "staff"),
    ("manager", "manager"),
)

def _role_to_group_name(role: str) -> str:
    return role.capitalize()  # "staff" -> "Staff", "manager" -> "Manager"

def _infer_role_from_groups(user: AbstractUser) -> str:
    if user.groups.filter(name="Manager").exists():
        return "manager"
    if user.groups.filter(name="Staff").exists():
        return "staff"
    # fallback by is_staff (for safety)
    return "manager" if getattr(user, "is_staff", False) else "staff"


class UserBaseSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        fields = ("id", "username", "email", "first_name", "last_name", "role", "is_active", "date_joined")
        read_only_fields = ("id", "role", "date_joined")

    def get_role(self, obj: AbstractUser) -> str:
        return _infer_role_from_groups(obj)


class UserCreateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=ROLE_CHOICES, write_only=True)  # ← add write_only=True
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = UserModel
        fields = ("id", "username", "email", "first_name", "last_name", "role", "password")

    def create(self, validated_data: dict[str, Any]) -> AbstractUser:
        role = validated_data.pop("role")
        pwd = validated_data.pop("password")
        user: AbstractUser = UserModel.objects.create(**validated_data)
        user.set_password(pwd)
        if role == "manager":
            user.is_staff = True
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

        # Disallow changing superusers here
        if getattr(instance, "is_superuser", False) and role is not None:
            raise serializers.ValidationError({"role": "Cannot change role of a superuser."})

        # Apply basic fields
        for k, v in validated_data.items():
            setattr(instance, k, v)

        if role is not None:
            # update is_staff based on role
            instance.is_staff = (role == "manager")
            # update groups
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