# Tasks

Implement all five. Do not edit anything under `tests/`; the acceptance suite
is fixed and is hashed before and after the run.

1. **Percent discount** — in `app/pricing.py`, `discount_for(subtotal)` returns
   10% of the subtotal when the subtotal is at least 100, otherwise 0.
2. **Sales tax** — `pricing.tax_for(subtotal)` returns 8% of the amount it is
   given. `Cart.total()` must be `(subtotal - percent discount - coupon) * 1.08`.
3. **Coupons** — `pricing.coupon_discount(code, subtotal)` supports `SAVE5`
   (5.0 off) and `HALF` (50% off); unknown codes give 0. `Cart` takes an
   optional `coupon: str | None` and applies it after the percent discount and
   before tax.
4. **CSV export** — `app/report.py` gains `to_csv(items)` returning the header
   `sku,price,qty` followed by one line per item. `Cart.to_csv()` delegates to
   it.
5. **Money rounding** — add `app/money.py` with `round_money(value)` returning a
   float rounded to 2 decimals; use it for the final `Cart.total()` value.
