# Razorpay Chargeback and Dispute Process Overview
A chargeback is a forced reversal of a transaction initiated by a customer via their bank, typically due to claims of fraud, non-delivery of goods, or dissatisfaction.

Disputes generally move through these stages:
- **Fraud**: Raised when a bank suspects a transaction is fraudulent.
- **Retrieval**: A "soft" chargeback where the issuer requests additional information.
- **Chargeback**: The official inquiry phase where funds are debited from the merchant's account.
- **Pre-Arbitration/Arbitration**: If a merchant wins a chargeback but the customer challenges it again, the case moves to these advanced, costlier stages involving card networks.

The "Deduct at Onset" (DAO) Mechanism: For many transactions (especially international), Razorpay may debit the disputed amount from the merchant's account immediately upon the initiation of a dispute.

# Merchant Guidelines for Managing Disputes
To successfully contest a chargeback, merchants must act quickly and provide precise documentation.

1. **Respond Promptly**: Every dispute has a strict deadline (often as short as 3 business days for international transactions). Failure to respond within the stipulated time will result in an automatic loss of the dispute.
2. **Submit Compelling Evidence**: The key to winning is providing documents that specifically address the chargeback reason code assigned to the dispute.
3. **Use the Dashboard**: Merchants should manage all dispute responses through the Razorpay Dispute Dashboard. This tool tracks statuses (Open, Under Review, Won, Lost, Closed) and allows for the upload of evidence.
4. **Avoid Excessive Disputes**: Card networks monitor chargeback ratios. Consistently high dispute rates can lead to fines, higher processing fees, or the suspension of your payment gateway account.

# Non-Receipt of Goods or Services
This category covers claims that the customer never received the goods or services.
To contest this successfully, merchants must provide proof of delivery. This includes:
- Tracking details showing delivery to the correct address.
- Shipping confirmation documents.
- Delivery logs or signed delivery receipts by the customer.
- If the item is digital, logs showing the customer downloaded or accessed the service from their IP address.

# Services or Merchandise Not as Described
This category covers claims that the product was defective, damaged, or not as described, or quality-related issues.
To contest this successfully, merchants must provide:
- Clear product descriptions or images proving the item matched what was advertised.
- Customer communication logs showing attempts to resolve the issue or where the customer acknowledges the product was as described.
- Proof that the customer did not attempt to return the item, or that a return was processed in accordance with your public policies.

# Credit Not Processed
This covers claims where a customer returned an item or cancelled a service but claims they never received their refund.
To contest this successfully, merchants must provide:
- Proof of the refund transaction, showing it was credited back to the original payment method.
- Communication logs showing a refund agreement or explaining why a refund was denied based on the merchant's cancellation/refund policy.
- A copy of the cancellation/refund policy that the customer agreed to at checkout.

# Fraud or Unauthorized Transaction
This covers claims of unauthorized transactions or stolen card details.
To contest this successfully, merchants must provide:
- Proof of delivery or service usage (IP address, login logs).
- Customer history showing previous undisputed transactions.
- AVS (Address Verification System) or CVV match information.
- Evidence that 3D Secure (3DS) or strong customer authentication was used during the transaction.

# Processing Error
This covers technical issues such as duplicate charges or incorrect amounts being charged.
To contest this successfully, merchants must provide:
- Transaction logs showing only a single charge was authorized.
- An itemized invoice proving the total amount charged matches what the customer agreed to.
- Evidence that any duplicate charges were already refunded to the customer.
