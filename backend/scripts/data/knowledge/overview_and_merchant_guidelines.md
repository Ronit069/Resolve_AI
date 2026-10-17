# Razorpay Chargeback and Dispute Process Overview

A chargeback is a forced reversal of a transaction initiated by a customer through their issuing bank, typically on claims of fraud, non-delivery, or dissatisfaction with a purchase. Unlike a refund, a chargeback is adjudicated by the card network and the customer's bank, not by the merchant directly.

## Dispute Lifecycle Stages

Disputes generally move through these stages, each with its own deadline and evidence expectations:

- **Fraud Alert / Early Warning**: An advisory signal (e.g. Visa's TC40 / Mastercard's SAFE) that a card may have been compromised. No funds move yet; merchants can sometimes refund proactively to avoid an escalation to a formal chargeback.
- **Retrieval Request**: A "soft" chargeback where the issuer asks for supporting documentation (invoice, proof of delivery) before deciding whether to escalate. Responding well here can prevent a chargeback from being filed at all.
- **Chargeback (First Presentment)**: The formal inquiry phase. Funds are provisionally debited from the merchant's account. The merchant has a fixed window to submit representment evidence contesting the reversal.
- **Representment / Second Presentment**: The merchant's formal rebuttal, submitted with evidence, asking the issuer to re-examine the claim and return the funds.
- **Pre-Arbitration**: If the issuer rejects the representment, it may offer a pre-arbitration chance to settle before escalating to the card network.
- **Arbitration**: The card network itself rules on the case. This stage is costly (arbitration fees apply to whichever side loses) and is reserved for disputes where the merchant has strong documentary evidence and a clear belief the chargeback is invalid.

## The "Deduct at Onset" (DAO) Mechanism

For many transactions -- especially cross-border/international ones -- Razorpay may debit the disputed amount from the merchant's account immediately upon the dispute being raised, rather than waiting for a ruling. If the merchant later wins the dispute, the amount is credited back. This means a merchant's available balance can be affected before any evidence has even been reviewed, which is why fast evidence submission matters even when a merchant is confident they will win.

## Merchant Guidelines for Managing Disputes

To successfully contest a chargeback, merchants must act quickly and provide precise, reason-code-specific documentation.

1. **Respond promptly.** Every dispute has a strict deadline -- often as short as 3-7 business days for the first response, shorter for international transactions. Missing the deadline results in an automatic loss regardless of the merits of the case; no evidence submitted after the deadline will be considered.
2. **Match evidence to the reason code.** Generic evidence (a plain receipt, a vague email) rarely wins. The evidence must directly rebut the *specific* claim the reason code represents -- see the reason-code-specific guidance documents for what each category actually requires.
3. **Use the Razorpay Dispute Dashboard.** All dispute responses should be managed through the dashboard, which tracks status (Open, Under Review, Won, Lost, Closed) and is the only channel for evidence upload that is recognized in the formal process.
4. **Keep records contemporaneously, not retroactively.** Evidence gathered at the time of the transaction (automated shipping confirmations, system-generated logs) carries far more weight than evidence assembled after a dispute is already filed, since the latter can appear self-serving to an issuer's reviewer.
5. **Monitor the chargeback ratio.** Card networks monitor chargeback-to-transaction ratios per merchant. Consistently exceeding a network's threshold (commonly in the 0.9%-1% range, varying by network and program) can result in excess-chargeback fines, higher processing fees, or termination from the merchant's acquiring bank -- independent of whether individual disputes are won or lost.
6. **Do not contest a dispute with no real evidence.** A speculative representment that is rejected still costs processing time and, at the pre-arbitration/arbitration stage, real fees. If there is genuinely no evidence to rebut the claim, accepting the loss early is often the better business decision.
