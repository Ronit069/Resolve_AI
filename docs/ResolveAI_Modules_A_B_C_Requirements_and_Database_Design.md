<!-- Converted from the original DOCX for IDE-agent readability. Embedded diagrams are represented by their figure captions; structured tables and text are preserved. -->

# ResolveAI - Foundation Modules A, B and C

Overall Implementation Flow, Detailed Requirements and Database Design

Razorpay AI Risk Manager Hackathon - Chargeback Evidence Verifier & Auto-Responder

Scope: Module A - Secure Dispute Ingestion | Module B - Transaction and Order Enrichment | Module C - Evidence Intake and File Security

| Purpose of this document: give students an implementation-ready foundation before OCR, cross-document validation, ML, RAG and LLM response generation. The attached Razorpay brief requires a working defense-only solution with measured precision/recall and false-positive cost; the A-B-C modules below are the engineering foundation proposed to support that objective. |
| --- |

# 1. Overall Flow of Module A -> Module B -> Module C

*Figure 1. ResolveAI trusted-input foundation before OCR and ML.*

## 1.1 End-to-End Processing Sequence

1.  Receive a Razorpay webhook or a synthetic dispute event through Module A.

2.  Verify authenticity, validate schema, detect duplicates and create the internal case_id.

3.  Persist the canonical dispute and enqueue enrichment.

4.  Module B fetches/loads payment, order, shipment, refund and privacy-minimised customer history.

5.  Validate entity relationships and construct the canonical transaction timeline.

6.  Module C accepts evidence files only for an authorised case and merchant.

7.  Quarantine, validate file signature/MIME, enforce size limits, scan for malware, hash and deduplicate the file.

8.  Store safe files in object storage and evidence metadata in PostgreSQL.

9.  Mark the case EVIDENCE_READY when the minimum evidence intake criteria are satisfied.

10.  Pass only trusted structured case metadata and safe files to the later OCR/document-intelligence module.

## 1.2 Required State Progression

```text
RECEIVED
  v
VALIDATED
  v
INGESTED
  v
ENRICHING
  v
ENRICHED
  v
AWAITING_EVIDENCE
  v
EVIDENCE_READY
  v
[Later: OCR_PROCESSING -> FEATURE_READY -> RISK_SCORED -> REVIEW/ACCEPT/CONTEST]
```

## 1.3 Shared Output Contract after Module C

```text
CanonicalCase {
  case_id,
  merchant_id,
  dispute,
  payment,
  order,
  shipment,
  refunds[],
  customer_history,
  timeline,
  consistency_flags[],
  completeness,
  evidence_documents[],
  processing_state
}
```

# 2. Shared Requirements Across Modules A, B and C

| ID | Requirement | Priority | Acceptance / Validation |
| --- | --- | --- | --- |
| SH-01 | Use one immutable internal case_id as the central key across all modules. | Must | A dispute received once produces exactly one case record; all enrichment/evidence records reference it. |
| SH-02 | Use canonical internal schemas instead of leaking provider-specific payload structures downstream. | Must | Module B/C operate without depending on raw Razorpay JSON field names. |
| SH-03 | Use common authentication, RBAC and merchant isolation. | Must | A user from merchant X cannot read/write merchant Y cases or evidence. |
| SH-04 | Use shared idempotency rules for webhook, enrichment jobs and uploaded documents. | Must | Repeated input does not create duplicated business records or duplicate processing. |
| SH-05 | Use a single audit service for security-sensitive and business-state changes. | Must | Every important create/update/view/submit action is traceable by case_id, actor and timestamp. |
| SH-06 | Use UTC in database and backend; convert only at presentation layer. | Must | No ambiguity in deadlines or event ordering. |
| SH-07 | Use structured error codes and retryability flags. | Must | Client/worker can distinguish validation failure, transient external failure and permanent failure. |
| SH-08 | Use correlation/request IDs in logs and queue messages. | Should | One case can be traced across API, worker and storage operations. |
| SH-09 | Minimise PII and never store/log CVV, full card data, secrets or raw credentials. | Must | Security review finds no prohibited sensitive data. |
| SH-10 | Configuration and secrets come from environment/secret manager. | Must | No API keys or secrets committed in repository. |

## 2.1 Shared Database Foundation

| Table | Purpose | Primary Key | Important Foreign Keys / Constraints |
| --- | --- | --- | --- |
| merchants | Tenant/merchant identity and status. | merchant_id | unique external_merchant_id; status indexed |
| app_users | Authenticated internal/merchant users. | user_id | merchant_id -> merchants; unique email/login per tenant |
| cases | Central aggregate for one dispute-defense case. | case_id | merchant_id -> merchants; external_dispute_id UNIQUE per source |
| audit_logs | Immutable activity/security trail. | audit_id | case_id optional -> cases; user_id optional -> app_users |
| processing_errors | Standardized module failures. | error_id | case_id -> cases; module + error_code + retryable |

### 2.2 Core cases Table

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| case_id | UUID / VARCHAR(36) | PK, NOT NULL | Internal immutable ResolveAI case identifier. |
| merchant_id | UUID / VARCHAR | FK, NOT NULL | Owning merchant/tenant. |
| external_dispute_id | VARCHAR(100) | NOT NULL | Razorpay/synthetic dispute ID. |
| source | VARCHAR(30) | NOT NULL | razorpay, synthetic, import, etc. |
| processing_state | VARCHAR(40) | NOT NULL | Current A->B->C state. |
| risk_state | VARCHAR(40) | NULL initially | Reserved for later ML decision state. |
| created_at | TIMESTAMPTZ | NOT NULL | Case creation time in UTC. |
| updated_at | TIMESTAMPTZ | NOT NULL | Latest update time in UTC. |

# 3. Module A - Secure Dispute Ingestion

## 3.1 Objective

Receive a dispute event securely, prove that the event is acceptable for processing, convert it to a canonical dispute structure, persist it exactly once, and trigger asynchronous enrichment. Module A must remain fast and must not run OCR, ML or LLM workloads inside the webhook request.

## 3.2 Inputs

| Input | Required | Notes |
| --- | --- | --- |
| Raw HTTP request body | Yes | Required for signature/HMAC verification; preserve raw bytes. |
| Webhook signature header | Yes in Razorpay mode | Reject if verification fails. |
| External event ID | Yes | Primary idempotency key for webhook delivery. |
| Event type | Yes | Example: dispute created/updated/outcome event. |
| Dispute payload | Yes | Contains dispute/payment/amount/reason/status/timestamps. |
| Synthetic source marker | Dev mode | Allows identical downstream flow without real Razorpay data. |

## 3.3 Detailed Functional and Security Requirements

| ID | Requirement | Priority | Acceptance / Validation |
| --- | --- | --- | --- |
| A-01 | Expose a dedicated webhook endpoint and preserve the raw request body. | Must | Valid signed event reaches validation pipeline without payload re-serialization. |
| A-02 | Verify webhook signature before any business processing. | Must | Invalid signature produces 401/403 and no case/dispute record. |
| A-03 | Deduplicate events using external event_id and a UNIQUE database constraint. | Must | Same event sent repeatedly creates one event record and one business transition. |
| A-04 | Validate mandatory dispute fields using a typed schema (e.g., Pydantic). | Must | Missing/invalid amount, IDs, currency or timestamps are rejected/isolated. |
| A-05 | Create or update the canonical dispute record under a deterministic case_id policy. | Must | Downstream modules consume internal fields, not raw provider JSON. |
| A-06 | Validate permitted dispute state transitions and tolerate out-of-order events. | Must | Older/stale event cannot incorrectly regress the current state. |
| A-07 | Persist raw event metadata and payload hash for traceability. | Should | Support audit/debug without exposing secrets. |
| A-08 | Acknowledge accepted webhook quickly after durable persistence/queueing. | Must | No OCR/ML blocking the webhook response. |
| A-09 | Enqueue exactly one enrichment task per required transition. | Must | Queue deduplication/job idempotency prevents duplicate Module B execution. |
| A-10 | Create structured security and audit logs. | Must | Log event_id, case_id, result, actor/system, duration; never log secrets. |
| A-11 | Rate limit and enforce request-size limits. | Should | Abusive traffic does not exhaust service resources. |
| A-12 | Route invalid-but-inspectable events to a dead-letter/error record. | Should | Failed events are observable and can be reprocessed safely if corrected. |

## 3.4 Processing Flow

Webhook / Synthetic Event
        v
Capture raw body + headers
        v
Verify signature/source
        v
Check event_id idempotency
        v
Validate canonical schema
        v
Validate state transition
        v
Persist webhook_event + dispute + case state
        v
Write audit log
        v
Enqueue ENRICH_DISPUTE(case_id)
        v
Return success response

## 3.5 Module A Output

```text
{
  "case_id": "CASE-00001",
  "external_dispute_id": "DISPUTE-101",
  "payment_id": "PAY-901",
  "ingestion_status": "SUCCESS",
  "security_verified": true,
  "processing_state": "INGESTED",
  "next_action": "TRANSACTION_ENRICHMENT"
}
```

## 3.6 Module A Database Design

### Table A1 - webhook_events

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| webhook_event_pk | BIGSERIAL | PK | Internal row identity. |
| external_event_id | VARCHAR(120) | UNIQUE, NOT NULL | Webhook idempotency key. |
| event_type | VARCHAR(80) | NOT NULL, INDEX | Event routing/type. |
| source | VARCHAR(30) | NOT NULL | razorpay/synthetic. |
| payload_hash | CHAR(64) | NOT NULL | SHA-256 of raw payload for integrity/debug. |
| signature_verified | BOOLEAN | NOT NULL | Security verification result. |
| received_at | TIMESTAMPTZ | NOT NULL, INDEX | Receipt time. |
| processed_at | TIMESTAMPTZ | NULL | Successful processing time. |
| status | VARCHAR(30) | NOT NULL | RECEIVED/PROCESSED/DUPLICATE/REJECTED/FAILED. |
| case_id | UUID/VARCHAR | FK -> cases, NULL until mapped | Link to business case. |

### Table A2 - disputes

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| dispute_pk | BIGSERIAL | PK | Internal dispute row. |
| case_id | UUID/VARCHAR | FK -> cases, UNIQUE | One canonical dispute per current case MVP. |
| external_dispute_id | VARCHAR(120) | NOT NULL, INDEX | Provider dispute ID. |
| payment_id | VARCHAR(120) | NOT NULL, INDEX | Link used by Module B. |
| amount_minor | BIGINT | >0 | Amount in minor units to avoid floating-point errors. |
| currency | CHAR(3) | NOT NULL | ISO-style currency code. |
| reason_code | VARCHAR(100) | NOT NULL, INDEX | Drives evidence requirements. |
| phase | VARCHAR(60) | NULL | Dispute lifecycle phase. |
| status | VARCHAR(60) | NOT NULL, INDEX | Current dispute status. |
| dispute_created_at | TIMESTAMPTZ | NOT NULL | Provider dispute time. |
| respond_by | TIMESTAMPTZ | NULL, INDEX | Merchant response deadline. |
| source_updated_at | TIMESTAMPTZ | NULL | Used to handle out-of-order events. |

### Table A3 - dispute_events

| Column | Type | Constraints | Purpose |
| --- | --- | --- | --- |
| dispute_event_pk | BIGSERIAL | PK | Event history row. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| external_event_id | VARCHAR(120) | FK/REFERENCE webhook event | Trace to ingestion. |
| old_status | VARCHAR(60) | NULL | Prior state. |
| new_status | VARCHAR(60) | NOT NULL | New state. |
| event_time | TIMESTAMPTZ | NOT NULL, INDEX | Business/source event time. |
| accepted_transition | BOOLEAN | NOT NULL | Whether state mutation was applied. |
| reason | VARCHAR(255) | NULL | Why event was rejected/ignored/accepted. |

## 3.7 Module A Key Constraints and Indexes

- UNIQUE external_event_id: hard protection against duplicate webhook processing.

- UNIQUE case_id in disputes: prevents accidental duplicate current dispute aggregates in the MVP.

- Index payment_id: Module B starts enrichment from payment_id.

- Index respond_by: supports deadline dashboards and urgency jobs.

- Index received_at/event_time: supports operational troubleshooting and ordering checks.

## 3.8 Module A Acceptance Tests

| Scenario | Expected Result |
| --- | --- |
| Valid signed new dispute event | Create/resolve case_id, store event/dispute, enqueue enrichment. |
| Invalid signature | Reject; no business record mutation. |
| Exact duplicate event | Return safe acknowledgement; do not duplicate case/job. |
| Missing payment_id or invalid amount | Validation failure with structured error. |
| Stale/out-of-order status update | Preserve correct current state; record ignored transition. |
| Database/queue transient failure | Return/retry according to configured delivery policy; no partial duplicate state. |

# 4. Module B - Transaction and Order Enrichment

## 4.1 Objective

Convert a minimal dispute into a complete transaction context by retrieving/loading payment, order, shipment, refund and privacy-minimised customer history. Module B must also validate relationships, construct a timeline, calculate completeness, and clearly represent missing information as UNKNOWN rather than silently converting it into a negative signal.

## 4.2 Inputs and Data Sources

| Entity | Initial Development Source | Later/Production Source | Key Join |
| --- | --- | --- | --- |
| Payment | Synthetic payments dataset | Razorpay payment API / trusted backend | dispute.payment_id = payment.id |
| Order | Synthetic orders dataset | Razorpay order API + merchant backend | payment.order_id = order.id |
| Shipment | Synthetic shipments dataset | Merchant OMS/logistics API | shipment.order_id = order.id |
| Refund | Synthetic refunds dataset | Razorpay/merchant refund source | refund.payment_id = payment.id |
| Customer history | Synthetic aggregate table | Merchant CRM/order history | privacy-safe customer reference |

## 4.3 Detailed Requirements

| ID | Requirement | Priority | Acceptance / Validation |
| --- | --- | --- | --- |
| B-01 | Consume ENRICH_DISPUTE(case_id) asynchronously and idempotently. | Must | Repeated task updates existing enrichment snapshot without duplicate rows. |
| B-02 | Use provider adapters/interfaces for payment, order, shipment, refund and customer data. | Must | Synthetic and real/test adapters are interchangeable downstream. |
| B-03 | Retrieve payment using the payment_id created in Module A. | Must | Payment is stored/normalized or a controlled not-found error is recorded. |
| B-04 | Retrieve associated order and normalize merchant/order references. | Must | Order association is explicit and validated. |
| B-05 | Retrieve shipment and refund information when available. | Must | Absence is represented as UNKNOWN/NOT_AVAILABLE, not false. |
| B-06 | Use privacy-minimised customer history aggregates rather than unnecessary raw PII. | Must | Feature-ready counts/rates available without exposing full customer identity. |
| B-07 | Validate dispute->payment->order->shipment/refund relationships. | Must | Mismatch creates explicit consistency flag and prevents silent contamination. |
| B-08 | Calculate financial consistency fields and net amount after refunds. | Must | Partial refund and multi-record refund cases calculate correctly. |
| B-09 | Construct canonical transaction timeline and derived time intervals. | Must | Timeline fields are UTC and internally consistent. |
| B-10 | Calculate per-entity and overall enrichment completeness. | Must | Missing fields are measurable for later risk features and UI. |
| B-11 | Implement retries with timeout, exponential backoff and maximum attempts for transient sources. | Must | Temporary service failures do not permanently fail immediately. |
| B-12 | Cache stable provider responses where safe. | Should | Repeated reads avoid unnecessary external calls. |
| B-13 | Persist enrichment version/source timestamps for reproducibility. | Should | Later ML prediction can reconstruct which data snapshot was used. |
| B-14 | Transition case state to ENRICHED only when minimum critical enrichment succeeds. | Must | State machine accurately represents readiness. |

## 4.4 Relationship and Timeline Validation Rules

| Rule | Example Validation | Failure Flag |
| --- | --- | --- |
| Dispute <-> Payment | dispute.payment_id == payment.external_payment_id | PAYMENT_MISMATCH |
| Payment <-> Order | payment.external_order_id == order.external_order_id | ORDER_MISMATCH |
| Order <-> Shipment | shipment.external_order_id == order.external_order_id | SHIPMENT_MISMATCH |
| Payment <-> Refund | refund.external_payment_id == payment.external_payment_id | REFUND_MISMATCH |
| Financial | disputed amount <= captured/net amount subject to business rules | AMOUNT_INCONSISTENT |
| Temporal | order <= payment/capture <= dispatch <= delivery <= dispute when fields exist | TIMELINE_INCONSISTENT |

## 4.5 Module B Output

```text
EnrichedCase {
  case_id,
  dispute,
  payment,
  order,
  shipment,
  refunds[],
  customer_history,
  timeline {
    order_to_payment_minutes,
    payment_to_dispatch_hours,
    dispatch_to_delivery_hours,
    delivery_to_dispute_days
  },
  consistency_flags[],
  completeness {
    payment,
    order,
    shipment,
    refund,
    customer_history,
    overall
  },
  processing_state: "ENRICHED"
}
```

## 4.6 Module B Database Design

### Table B1 - payments

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| payment_pk | BIGSERIAL | PK | Internal row. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case association. |
| external_payment_id | VARCHAR(120) | UNIQUE/INDEX | Provider payment ID. |
| external_order_id | VARCHAR(120) | INDEX, NULL allowed | Join to order. |
| amount_minor | BIGINT | NOT NULL | Authorized/captured amount in minor units. |
| currency | CHAR(3) | NOT NULL | Currency. |
| status | VARCHAR(50) | NOT NULL | Payment status. |
| method | VARCHAR(50) | NULL | Card/UPI/netbanking/etc. if available. |
| captured | BOOLEAN | NULL | Capture indicator. |
| created_at_source | TIMESTAMPTZ | NULL | Provider payment time. |
| fetched_at | TIMESTAMPTZ | NOT NULL | Snapshot time. |

### Table B2 - orders

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| order_pk | BIGSERIAL | PK | Internal row. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| external_order_id | VARCHAR(120) | INDEX | Provider/merchant order ID. |
| merchant_order_ref | VARCHAR(120) | INDEX, NULL | Merchant business reference. |
| order_amount_minor | BIGINT | NULL | Order amount. |
| currency | CHAR(3) | NULL | Currency. |
| order_status | VARCHAR(50) | NULL | Current order status. |
| customer_ref_hash | CHAR(64) | NULL, INDEX | Privacy-safe customer linkage. |
| created_at_source | TIMESTAMPTZ | NULL | Order creation time. |
| fetched_at | TIMESTAMPTZ | NOT NULL | Snapshot time. |

### Table B3 - shipments

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| shipment_pk | BIGSERIAL | PK | Shipment row. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| external_order_id | VARCHAR(120) | INDEX | Order relationship. |
| shipment_id | VARCHAR(120) | UNIQUE/INDEX | Merchant/logistics shipment ID. |
| courier | VARCHAR(100) | NULL | Courier/service. |
| tracking_id | VARCHAR(150) | INDEX, NULL | Tracking identifier. |
| dispatch_at | TIMESTAMPTZ | NULL | Dispatch time. |
| delivery_at | TIMESTAMPTZ | NULL | Delivery time. |
| delivery_status | VARCHAR(50) | NULL | Delivered/in-transit/failed/etc. |
| delivery_address_hash | CHAR(64) | NULL | Privacy-safe comparison value. |
| recipient_confirmation | BOOLEAN | NULL | If source provides confirmation. |

### Table B4 - refunds

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| refund_pk | BIGSERIAL | PK | Refund row. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| external_refund_id | VARCHAR(120) | UNIQUE/INDEX | Refund ID. |
| external_payment_id | VARCHAR(120) | INDEX | Payment relationship. |
| refund_amount_minor | BIGINT | >0 | Refund amount. |
| status | VARCHAR(50) | NOT NULL | Refund status. |
| refund_reason | VARCHAR(255) | NULL | Reason/category if available. |
| refund_at | TIMESTAMPTZ | NULL | Refund time. |

### Table B5 - customer_history

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| customer_history_pk | BIGSERIAL | PK | Aggregate row. |
| case_id | UUID/VARCHAR | FK -> cases, UNIQUE | One snapshot per case/version in MVP. |
| customer_ref_hash | CHAR(64) | INDEX | Privacy-safe identity. |
| account_age_days | INT | NULL | Age/tenure. |
| previous_order_count | INT | >=0 | Historical order count. |
| previous_dispute_count | INT | >=0 | Historical dispute count. |
| previous_refund_count | INT | >=0 | Historical refund count. |
| refund_rate | DECIMAL(6,5) | NULL | Derived historical rate. |
| dispute_rate | DECIMAL(6,5) | NULL | Derived historical rate. |
| snapshot_at | TIMESTAMPTZ | NOT NULL | Reproducibility timestamp. |

### Table B6 - case_enrichment

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| enrichment_pk | BIGSERIAL | PK | Snapshot/summary row. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| version | INT | NOT NULL | Enrichment version. |
| payment_complete | DECIMAL(5,4) | 0..1 | Payment completeness. |
| order_complete | DECIMAL(5,4) | 0..1 | Order completeness. |
| shipment_complete | DECIMAL(5,4) | 0..1 | Shipment completeness. |
| refund_complete | DECIMAL(5,4) | 0..1 | Refund completeness. |
| customer_complete | DECIMAL(5,4) | 0..1 | Customer-history completeness. |
| overall_complete | DECIMAL(5,4) | 0..1 | Overall completeness. |
| consistency_flags | JSONB | NOT NULL default [] | Explicit validation flags. |
| timeline_json | JSONB | NOT NULL default {} | Canonical derived timeline. |
| created_at | TIMESTAMPTZ | NOT NULL | Snapshot creation time. |

## 4.7 Module B Acceptance Tests

| Scenario | Expected Result |
| --- | --- |
| Payment/order available and linked | Store normalized records; state advances. |
| Payment not found | Controlled permanent/diagnostic failure; no fabricated values. |
| Shipment source unavailable | Retry; if exhausted mark shipment UNKNOWN and record error according to policy. |
| Partial refund exists | Net amount and refund totals are correct. |
| Order ID mismatch | Create ORDER_MISMATCH flag; do not silently overwrite. |
| Repeated enrichment job | No duplicate business entities; version/update behavior deterministic. |
| Missing optional customer history | Continue with explicit completeness reduction. |

# 5. Module C - Evidence Intake and File Security

## 5.1 Objective

Allow an authorised merchant/reviewer to attach chargeback evidence to the correct case while preventing unsafe, oversized, corrupted, duplicate or cross-tenant files from entering the later OCR/AI pipeline. Module C manages secure intake and lifecycle; it does not yet interpret document semantics.

## 5.2 Supported Initial Evidence Types

| Evidence Type | Typical File | Use |
| --- | --- | --- |
| INVOICE | PDF/JPEG/PNG | Proof of order/amount/service. |
| PROOF_OF_DELIVERY | PDF/JPEG/PNG | Delivery/receipt confirmation. |
| COURIER_TRACKING | PDF/JPEG/PNG | Shipment timeline and tracking details. |
| REFUND_RECEIPT | PDF/JPEG/PNG | Proof of refund/credit. |
| CUSTOMER_COMMUNICATION | PDF/JPEG/PNG | Relevant merchant-customer communication. |
| SERVICE_CONFIRMATION | PDF/JPEG/PNG | Proof of service fulfillment. |
| TERMS_ACCEPTANCE | PDF/JPEG/PNG | Evidence of accepted terms/policy. |
| OTHER | PDF/JPEG/PNG | Restricted fallback requiring description/review. |

## 5.3 Detailed Requirements

| ID | Requirement | Priority | Acceptance / Validation |
| --- | --- | --- | --- |
| C-01 | Expose evidence upload API bound to case_id and authenticated merchant/user. | Must | Cross-tenant upload attempts return 403. |
| C-02 | Allow only configured evidence categories and file types. | Must | Unsupported document type or extension is rejected. |
| C-03 | Validate MIME/magic bytes in addition to file extension. | Must | Executable renamed .pdf is rejected. |
| C-04 | Enforce configurable per-file, file-count and per-case storage limits. | Must | Oversized abuse is rejected before expensive processing. |
| C-05 | Sanitize original filenames and store files using generated object keys/UUIDs. | Must | Path traversal and overwrite attacks are prevented. |
| C-06 | Place new uploads in quarantine before AI/OCR access. | Must | Unverified file cannot be consumed by OCR worker. |
| C-07 | Run malware scan (e.g., ClamAV) and capture scan result. | Must for final demo | Only CLEAN files can transition to READY_FOR_OCR. |
| C-08 | Calculate SHA-256 file hash for integrity and duplicate detection. | Must | Same case+hash is treated as duplicate according to policy. |
| C-09 | Use object storage (MinIO/S3) for binaries and PostgreSQL for metadata. | Must | Database does not store large file blobs in MVP. |
| C-10 | Use least-privilege access control for upload/view/download/delete/worker-read actions. | Must | Evidence access is role and tenant controlled. |
| C-11 | Maintain an explicit evidence lifecycle state machine. | Must | Every file is traceable from UPLOADED to READY_FOR_OCR or failure state. |
| C-12 | Maintain reason-code-aware required/recommended evidence checklist. | Must | UI/backend can identify missing evidence categories without semantic OCR. |
| C-13 | Record immutable upload/scan/view/delete/replace audit events. | Must | Security-sensitive evidence operations are attributable. |
| C-14 | Generate short-lived/signed download access rather than public object URLs. | Should | Evidence is not publicly accessible. |

## 5.4 Evidence Lifecycle

UPLOADED
   v
QUARANTINED
   v
FILE_VALIDATION
   v
MALWARE_SCANNING
   v
VERIFIED
   v
READY_FOR_OCR

Failure states:
INVALID_FORMAT | TOO_LARGE | CORRUPTED | INFECTED | SCAN_FAILED | DUPLICATE | REJECTED

## 5.5 Suggested Initial File Limits

| Setting | Suggested MVP Value | Implementation Note |
| --- | --- | --- |
| Allowed formats | PDF, JPEG, PNG | Configurable allowlist. |
| Max individual file | 10 MB | Reject before storing long-term. |
| Max files per case | 15 | Configurable. |
| Max total evidence per case | 50 MB | Configurable quota. |
| Object visibility | Private | Access only through signed/authorized service path. |

## 5.6 Module C Output

```text
{
  "case_id": "CASE-001",
  "evidence": [
    {
      "document_id": "DOC-1",
      "type": "INVOICE",
      "scan_status": "CLEAN",
      "processing_status": "READY_FOR_OCR",
      "sha256": "..."
    }
  ],
  "evidence_summary": {
    "required": 4,
    "available": 3,
    "missing": ["PROOF_OF_DELIVERY"]
  },
  "processing_state": "EVIDENCE_READY"
}
```

## 5.7 Module C Database Design

### Table C1 - evidence_documents

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| document_id | UUID/VARCHAR | PK | Internal evidence ID. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Owning case. |
| merchant_id | UUID/VARCHAR | FK -> merchants, INDEX | Tenant boundary for defense-in-depth. |
| evidence_type | VARCHAR(60) | NOT NULL, INDEX | Controlled evidence category. |
| object_key | VARCHAR(500) | UNIQUE, NOT NULL | Private object-storage key. |
| original_filename | VARCHAR(255) | NULL | Sanitized display metadata. |
| mime_type | VARCHAR(100) | NOT NULL | Validated MIME. |
| file_size_bytes | BIGINT | NOT NULL | Quota/validation. |
| sha256 | CHAR(64) | NOT NULL, INDEX | Integrity/duplicate detection. |
| scan_status | VARCHAR(30) | NOT NULL | PENDING/CLEAN/INFECTED/FAILED. |
| processing_status | VARCHAR(40) | NOT NULL, INDEX | Evidence lifecycle state. |
| uploaded_by | UUID/VARCHAR | FK -> app_users | Actor. |
| uploaded_at | TIMESTAMPTZ | NOT NULL | Upload time. |

### Table C2 - malware_scan_results

| Column | Type | Constraints | Purpose |
| --- | --- | --- | --- |
| scan_id | BIGSERIAL | PK | Scan row. |
| document_id | UUID/VARCHAR | FK -> evidence_documents, INDEX | Evidence file. |
| scanner | VARCHAR(80) | NOT NULL | e.g., ClamAV. |
| scanner_version | VARCHAR(80) | NULL | Reproducibility. |
| scan_status | VARCHAR(30) | NOT NULL | CLEAN/INFECTED/FAILED. |
| signature_name | VARCHAR(255) | NULL | Threat signature if detected. |
| scanned_at | TIMESTAMPTZ | NOT NULL | Scan time. |
| duration_ms | INT | NULL | Operational metric. |

### Table C3 - evidence_requirements

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| requirement_id | BIGSERIAL | PK | Policy row. |
| reason_code | VARCHAR(100) | NOT NULL, INDEX | Dispute reason. |
| evidence_type | VARCHAR(60) | NOT NULL | Expected evidence category. |
| requirement_level | VARCHAR(20) | NOT NULL | REQUIRED/RECOMMENDED/OPTIONAL. |
| policy_version | VARCHAR(40) | NOT NULL | Versioned rule set. |
| active | BOOLEAN | NOT NULL | Current policy indicator. |

### Table C4 - case_evidence_status

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| case_evidence_status_pk | BIGSERIAL | PK | Summary row. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| reason_code | VARCHAR(100) | NOT NULL | Policy context. |
| required_count | INT | >=0 | Required categories. |
| available_required_count | INT | >=0 | Present safe required categories. |
| missing_required | JSONB | default [] | Missing required evidence list. |
| coverage_ratio | DECIMAL(5,4) | 0..1 | Simple evidence availability measure. |
| updated_at | TIMESTAMPTZ | NOT NULL | Latest checklist calculation. |

### Table C5 - evidence_access_events

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| evidence_event_pk | BIGSERIAL | PK | Evidence audit row. |
| document_id | UUID/VARCHAR | FK -> evidence_documents, INDEX | Document. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| user_id | UUID/VARCHAR | FK -> app_users, NULL for system | Actor. |
| action | VARCHAR(30) | NOT NULL | UPLOAD/VIEW/DOWNLOAD/DELETE/REPLACE/SCAN. |
| result | VARCHAR(30) | NOT NULL | SUCCESS/DENIED/FAILED. |
| occurred_at | TIMESTAMPTZ | NOT NULL, INDEX | Audit timestamp. |
| request_id | VARCHAR(100) | NULL, INDEX | Trace correlation. |

## 5.8 Module C Key Constraints and Indexes

- UNIQUE object_key: prevents object overwrite/collision.

- Recommended UNIQUE(case_id, sha256): prevents same evidence binary being attached repeatedly to the same case; relax if business explicitly needs versions.

- Index processing_status: OCR workers efficiently locate READY_FOR_OCR documents.

- Index case_id + evidence_type: fast checklist generation and case evidence UI.

- Private storage bucket: no public object URLs; use authorised/signed retrieval.

## 5.9 Module C Acceptance Tests

| Scenario | Expected Result |
| --- | --- |
| Valid PDF/JPEG/PNG from authorised merchant | Quarantine -> scan -> safe storage -> READY_FOR_OCR. |
| Executable renamed as PDF | MIME/magic validation rejects it. |
| File over configured size | Reject before expensive processing. |
| Malware detected | Keep quarantined/blocked; never expose to OCR. |
| Same file uploaded twice to same case | Duplicate detected using hash. |
| Merchant X uploads to Merchant Y case | 403 and audit record. |
| Corrupted PDF | Reject/mark CORRUPTED. |
| Safe file scan service temporarily fails | SCAN_FAILED/retry policy; file remains unavailable to OCR. |

# 6. Cross-Module Database Relationships

merchants
   │ 1
   ├────────────< app_users
   │
   └────────────< cases
                     │ 1
                     ├──────── 1 disputes
                     ├────────< dispute_events
                     ├────────< webhook_events
                     ├────────< payments
                     ├────────< orders
                     ├────────< shipments
                     ├────────< refunds
                     ├──────── 1 customer_history (MVP snapshot)
                     ├────────< case_enrichment
                     ├────────< evidence_documents
                     │              ├────────< malware_scan_results
                     │              └────────< evidence_access_events
                     ├────────< audit_logs
                     └────────< processing_errors

evidence_requirements is policy data keyed by reason_code and is joined to disputes.reason_code.

## 6.1 Recommended Integrity Rules

- Use foreign keys for all case-bound records; do not allow orphan payment/order/evidence rows.

- Use minor currency units (integer paise/cents) for amounts rather than floating-point money.

- Use soft deletion or immutable audit trails for sensitive evidence metadata; avoid silent hard deletion.

- Use optimistic version/state checks when multiple workers may update the same case.

- Every business mutation should update cases.updated_at and record an audit event.

- Use migration tooling (e.g., Alembic) from the first implementation day; do not manually evolve production schema.

# 7. Shared API Contracts

| API | Module | Purpose | Key Validation |
| --- | --- | --- | --- |
| POST /api/v1/webhooks/razorpay | A | Receive real dispute events. | Signature + event idempotency + schema. |
| POST /api/v1/dev/disputes | A (dev only) | Inject synthetic dispute through same canonical path. | Dev auth + schema; disable in production. |
| POST /api/v1/cases/{case_id}/enrich | B | Manual/retry enrichment trigger for development/admin. | Auth + case ownership + idempotent job. |
| GET /api/v1/cases/{case_id} | Shared | Return canonical case state/summary. | Auth + merchant isolation. |
| POST /api/v1/cases/{case_id}/evidence | C | Upload evidence. | RBAC + ownership + file security. |
| GET /api/v1/cases/{case_id}/evidence | C | List evidence metadata/status. | RBAC + tenant boundary. |

# 8. Recommended Student Implementation Order

1.  Freeze shared identifiers, canonical schemas, case state machine and database migrations.

2.  Create synthetic disputes/payments/orders/shipments/refunds matching the canonical schema.

3.  Implement Module A first with validation, idempotency and persistence.

4.  Implement Module B adapters in synthetic mode; validate joins and timeline.

5.  Implement Module C upload/quarantine/hash/MIME/scan/object-storage path.

```text
6.  Integrate A->B->C using one deterministic end-to-end test case.
```

7.  Add failure-path tests: duplicate webhook, missing payment, mismatched order, malware, wrong merchant, duplicate evidence.

8.  Only after EVIDENCE_READY is reliable, begin Module D (OCR + structured field extraction).

## 8.1 Minimum End-to-End Milestone

Synthetic dispute received
        v
CASE-00001 created exactly once
        v
Payment + order loaded
        v
Shipment/refund context attached
        v
Canonical timeline built
        v
Merchant uploads invoice + delivery proof
        v
Files validated, scanned, hashed and stored
        v
Evidence checklist updated
        v
CASE-00001.processing_state = EVIDENCE_READY

# 9. Definition of Done for the A-B-C Foundation

- One automated integration test executes the complete A->B->C happy path.

- At least one automated test exists for every high-risk rejection/failure path listed in this document.

- No raw secret, full card data or prohibited sensitive data appears in repository/log output.

- Database constraints protect against duplicates even if application-level idempotency fails.

- All files reaching READY_FOR_OCR have passed allowlist, MIME/magic validation, size checks, hashing and malware policy.

- All downstream modules can work from case_id and canonical data contracts without reading raw webhook JSON.

- The same pipeline supports synthetic development data today and can switch to Razorpay/merchant adapters later.

# 10. Handoff to the Next Module

The immediate next module after this foundation is Module D - Document Intelligence (OCR + layout/text extraction + structured evidence-field extraction). Module D should consume only evidence_documents with processing_status = READY_FOR_OCR and should write extraction results as versioned structured records; it should never read unverified/quarantined files.
