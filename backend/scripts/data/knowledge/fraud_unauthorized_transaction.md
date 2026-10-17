# Fraud or Unauthorized Transaction (Visa 10.4 and equivalents)

This category covers claims that the cardholder did not authorize the transaction -- most commonly "my card details were stolen" or "I never made this purchase." On Visa, this is most often filed under reason code 10.4 (Other Fraud -- Card-Absent Environment); other networks use their own equivalent codes. Always confirm the exact code shown on the Razorpay dashboard, since code numbering differs slightly by network and has changed over time with network rule updates.

## Why This Category Is Different

Unlike "not as described" or "non-receipt" disputes, a fraud claim is not really about the merchant's service -- it is about whether the person who placed the order was authorized to use the card. The merchant's job is to show the transaction was legitimate and properly authenticated, not to argue about product quality.

## Evidence That Actually Moves the Needle

- **3-D Secure (3DS) / strong customer authentication proof.** If the transaction went through 3DS (OTP, bank-side authentication) and the issuer approved it, this is often the single strongest piece of evidence, since liability for a successfully-authenticated transaction typically shifts away from the merchant under network rules.
- **AVS and CVV match results.** Address Verification System and CVV match status at the time of authorization show the person placing the order had the physical card and billing details, not just a stolen card number.
- **Device and session fingerprinting.** IP address, device ID, and browser fingerprint consistent with the cardholder's prior, undisputed purchases materially undermines a "someone else used my card" claim.
- **Delivery or usage evidence.** Proof the goods were delivered to (or the digital service was accessed from) an address/account historically associated with the genuine cardholder -- not a newly-added, unrelated shipping address, which is itself a common fraud signal.
- **Account history.** A pattern of prior successful, undisputed transactions on the same card/account is persuasive context, though never sufficient on its own.

## Common Mistakes That Lose This Category

- Submitting only a generic order confirmation email with no authentication/AVS/CVV data -- this proves an order was placed, not that the cardholder placed it.
- Ignoring a mismatched shipping address (different from any prior order) without addressing it directly in the representment.
- Assuming a successful payment capture alone proves authorization -- a stolen card can still pass basic processing if 3DS was not invoked or was bypassed.

## When Representment Is Unlikely to Succeed

If the transaction was never put through 3DS/strong authentication, had a mismatched AVS/CVV, shipped to a brand-new address, and has no prior purchase history to point to, the evidence available is usually too weak to win. In these cases, accepting the chargeback and investing in better fraud screening (3DS enforcement, AVS/CVV gating) for future orders is usually the better outcome than contesting.
