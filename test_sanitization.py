"""Validate IngestionClaimDTO input sanitization."""
import sys
sys.path.insert(0, "src")

from atlas.application.dto import IngestionClaimDTO, ProjectionDTO
from uuid import UUID

errors = 0

# Test 1: Simple value
c1 = IngestionClaimDTO(field_name="test", field_value="simple value")
assert c1.field_name == "test"
print("1. Simple value OK")

# Test 2: None value
c2 = IngestionClaimDTO(field_name="test", field_value=None)
assert c2.field_value is None
print("2. None value OK")

# Test 3: Empty field_name
try:
    IngestionClaimDTO(field_name="", field_value="x")
    print("3. FAIL: should have rejected empty field_name")
    errors += 1
except ValueError:
    print("3. Empty field_name rejected OK")

# Test 4: Oversized value (60KB)
big_val = "x" * (60 * 1024)
try:
    IngestionClaimDTO(field_name="test", field_value=big_val)
    print("4. FAIL: should have rejected oversized value")
    errors += 1
except ValueError:
    print("4. Oversized value rejected OK")

# Test 5: Deep nesting (21 levels)
deep = {}
current = deep
for i in range(21):
    current["x"] = {}
    current = current["x"]
current["x"] = "too deep"

try:
    IngestionClaimDTO(field_name="test", field_value=deep)
    print("5. FAIL: should have rejected deep nesting")
    errors += 1
except ValueError:
    print("5. Deep nesting rejected OK")

# Test 6: Acceptable nesting (8 levels) - should pass
acceptable = {}
current = acceptable
for i in range(8):
    current["x"] = {}
    current = current["x"]
current["x"] = "fine"

try:
    c6 = IngestionClaimDTO(field_name="test", field_value=acceptable)
    print("6. Acceptable nesting (8 levels) OK")
except ValueError as e:
    print(f"6. FAIL: should have accepted 8 levels: {e}")
    errors += 1

# Test 7: ProjectionDTO still works
p = ProjectionDTO(
    event_id=UUID("00000000-0000-0000-0000-000000000001"),
    projection_version=1,
    fields={},
    completeness_score=0.5,
    unresolved_conflict_fields=[],
    updated_at="2026-01-01T00:00:00",
)
assert p.projection_version == 1
print("7. ProjectionDTO OK")

if errors:
    print(f"\nFAILED: {errors} test(s)")
    sys.exit(1)
else:
    print("\n=== ALL INPUT SANITIZATION TESTS PASSED ===")