# procurement/tests/conftest.py
import pytest
from datetime import date
from decimal import Decimal
from inventory.models import InventoryEntry

@pytest.fixture
def make_inventory_item():
    """
    Factory: make_inventory_item("Bolt M8", quantity=Decimal("0"))
    Returns an InventoryEntry.
    """
    def _mk(name="Widget", **overrides):
        payload = {
            "date": date.today(),
            "truck_registration": "TEST-001",
            "quantity": Decimal("0"),
            # Map the test's 'name' to real fields:
            "mineral_or_equipment": name,
            "description": name,
        }
        payload.update(overrides)
        return InventoryEntry.objects.create(**payload)
    return _mk
