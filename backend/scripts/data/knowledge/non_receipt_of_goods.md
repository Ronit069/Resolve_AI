# Non-Receipt of Goods or Services (Visa 13.1 and equivalents)

This category covers claims that the customer never received the goods or services they paid for. On Visa, this commonly falls under reason code 13.1 (Merchandise/Services Not Received); confirm the exact code on the dashboard, as network rule changes have periodically consolidated related codes.

## What the Issuer Is Actually Checking

The issuer wants proof the merchant fulfilled its side of the transaction by the date the customer expected delivery (or, for services, by the agreed service date). The burden is on the merchant to show delivery/fulfillment happened, not on the customer to prove it didn't.

## Required Evidence by Scenario

**Physical goods:**
- Tracking details showing delivery to the address on the order, ideally with a carrier-confirmed delivery timestamp.
- A signed delivery receipt (proof of delivery / POD) where the carrier offers one -- strongest for higher-value items.
- Shipping confirmation generated automatically at time of dispatch (not reconstructed after the fact).

**Digital goods or services:**
- Access/download logs showing the customer's account retrieved the digital product, ideally correlated with an IP address or device consistent with the purchase.
- Login/usage logs for subscription or service-based products, showing the service was actually used after purchase.

**Split or partial shipments:**
- If an order shipped in multiple parcels, evidence must cover *all* parcels relevant to the disputed amount -- partial proof of delivery for only one of several packages is a common reason representments fail.

## Timing Considerations

Non-receipt disputes are often filed only after the customer's expected delivery window has passed, so the representment deadline can arrive with little runway. Merchants should pull tracking/log evidence as soon as the dispute appears in the dashboard rather than waiting, since carrier tracking portals sometimes purge or truncate historical data after a number of weeks.

## Common Mistakes That Lose This Category

- Submitting tracking showing the package *shipped* but not that it was *delivered* -- a dispatch confirmation alone does not rebut a non-receipt claim.
- Delivery confirmed to an address that does not match the billing/shipping address on the order, without an explanation (e.g. a locker/pickup point) addressed in the representment.
- For digital goods, providing purchase confirmation but no actual access/usage log after the purchase.
