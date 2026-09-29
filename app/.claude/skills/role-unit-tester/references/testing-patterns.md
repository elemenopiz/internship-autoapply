# Unit Testing Patterns Reference

## Test Data Builder Pattern (All Languages)

Instead of constructing complex test objects inline, use builders:
```
TestUser.builder()
    .withName("Alice")       // Override only what matters for THIS test
    .withRole("admin")
    .build()                 // Sensible defaults for everything else
```
Benefits: Tests show only relevant fields. Defaults prevent breakage on new fields.

## Boundary Value Analysis

### Numeric range [min, max]:
| Test | Value | Expected |
|------|-------|----------|
| Below min | min - 1 | Error |
| At min | min | Accept |
| Just above min | min + 1 | Accept |
| Nominal | (min+max)/2 | Accept |
| Just below max | max - 1 | Accept |
| At max | max | Accept |
| Above max | max + 1 | Error |
| Zero | 0 | Depends |
| Negative | -1 | Depends |

### String inputs:
| Test | Value | Expected |
|------|-------|----------|
| Empty | "" | Depends |
| Single char | "a" | Depends |
| At max length | "a" × max | Accept |
| Over max | "a" × (max+1) | Error |
| Unicode | "日本語テスト" | Accept |
| Special chars | `<script>alert(1)` | Sanitize/reject |
| Whitespace only | "   " | Depends |

## Framework-Specific Patterns

### Python (pytest)
```python
import pytest

class TestCalculateTotal:
    """Validates FR-001: Order total calculation"""

    def test_single_item_returns_item_price(self):
        order = Order(items=[Item(price=10.00, qty=1)])
        assert order.calculate_total() == 10.00

    def test_empty_items_returns_zero(self):
        order = Order(items=[])
        assert order.calculate_total() == 0.00

    @pytest.mark.parametrize("qty,expected", [(0, 0), (1, 10), (100, 1000)])
    def test_quantity_scaling(self, qty, expected):
        order = Order(items=[Item(price=10.00, qty=qty)])
        assert order.calculate_total() == expected

    def test_negative_price_raises_error(self):
        with pytest.raises(ValueError, match="Price must be non-negative"):
            Item(price=-1.00, qty=1)
```

### JavaScript/TypeScript (Jest/Vitest)
```typescript
describe('CartService', () => {
  describe('calculateTotal', () => {
    // Validates FR-001, AC-001-1
    it('should return sum of item prices × quantities', () => {
      const cart = new CartService();
      cart.addItem({ price: 10, qty: 2 });
      cart.addItem({ price: 5, qty: 1 });
      expect(cart.calculateTotal()).toBe(25);
    });

    it('should return 0 for empty cart', () => {
      const cart = new CartService();
      expect(cart.calculateTotal()).toBe(0);
    });

    it('should throw for negative quantity', () => {
      const cart = new CartService();
      expect(() => cart.addItem({ price: 10, qty: -1 }))
        .toThrow('Quantity must be non-negative');
    });
  });
});
```

### Go
```go
func TestCalculateTotal_SingleItem(t *testing.T) {
    // Validates FR-001, AC-001-1
    order := NewOrder([]Item{{Price: 10.00, Qty: 1}})
    got := order.CalculateTotal()
    want := 10.00
    if got != want {
        t.Errorf("CalculateTotal() = %v, want %v", got, want)
    }
}

func TestCalculateTotal_EmptyItems(t *testing.T) {
    order := NewOrder([]Item{})
    if got := order.CalculateTotal(); got != 0 {
        t.Errorf("CalculateTotal() = %v, want 0", got)
    }
}
```

### C (Unity)
```c
// Validates FR-001
void test_calculate_total_single_item(void) {
    item_t items[] = {{ .price = 1000, .qty = 1 }}; // cents
    order_t order = { .items = items, .count = 1 };
    TEST_ASSERT_EQUAL_INT(1000, calculate_total(&order));
}

void test_calculate_total_null_order_returns_error(void) {
    TEST_ASSERT_EQUAL_INT(ERR_NULL_PTR, calculate_total(NULL));
}
```

## Property-Based Testing
| Property | Example |
|----------|---------|
| Idempotent | `f(f(x)) == f(x)` — sort, normalize |
| Commutative | `f(a,b) == f(b,a)` — add, max |
| Inverse | `decode(encode(x)) == x` |
| Size preserving | `len(map(f, xs)) == len(xs)` |

Libraries: Hypothesis (Python), fast-check (JS/TS), proptest (Rust), QuickCheck (Haskell).
