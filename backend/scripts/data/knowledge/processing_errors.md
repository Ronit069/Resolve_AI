# Processing Errors (Visa 12.x family)

This category covers technical/procedural issues rather than disputes about the goods or service itself: duplicate charges, incorrect amounts, or currency conversion mismatches. Unlike the other categories, "processing error" is not a single reason code -- card networks split it across several related codes (commonly in Visa's 12.1-12.6 range, covering duplicate processing, incorrect transaction amount, and paid-by-other-means scenarios). Always confirm the exact code shown on the Razorpay dashboard for the specific dispute, since the right evidence differs by sub-type.

## Duplicate Charge Claims

The customer believes they were charged more than once for the same purchase.

- **Required evidence**: transaction logs covering the customer's account/card around the disputed date, showing only a single authorized and captured charge for that order. If a second charge exists, show it belongs to a *different* order (different items, different timestamp, different order ID).
- **Common mistake**: providing only the disputed transaction's own record without the surrounding transaction history needed to prove no duplicate exists.

## Incorrect Amount Claims

The customer believes they were charged a different amount than they agreed to.

- **Required evidence**: an itemized invoice or order summary showing exactly how the charged total was composed (item price, tax, shipping, any surcharges), matching what was presented to the customer before they confirmed payment.
- **Common mistake**: an invoice that doesn't reconcile line-by-line with the charged amount, or that reflects a price the customer never actually saw at checkout (e.g. a later price change).

## Already-Refunded Duplicate Charges

If a duplicate or erroneous charge was identified and refunded *before* the dispute was filed, the strongest evidence is the refund record with its date, shown to predate (or closely follow) the customer's awareness of the issue -- this often resolves the dispute without further escalation, since the network's real question is simply whether the customer was made whole.

## Currency and Conversion Mismatches

For cross-border transactions, a customer may dispute based on a different amount appearing on their statement due to currency conversion and the issuing bank's own conversion fees. Evidence should show the amount charged by the merchant in the transaction currency, and note (where relevant) that currency conversion and foreign transaction fees are applied by the customer's bank, not the merchant, and are outside the merchant's control.
