# ResolveAI — Module D: Document Intelligence

**Source:** ResolveAI - Module D: Document Intelligence Requirements & Database Design  
**Architecture:** Modules A → B → C → D → E

> Markdown conversion of the supplied Module D specification. The document defines Module D as the document-intelligence layer that consumes only trusted evidence from Module C and produces structured, traceable evidence for Module E.

# ResolveAI - Module D: Document Intelligence

Technical Requirements, Processing Flow and Database Design

Razorpay AI Risk Manager Hackathon - Chargeback Evidence Verifier & Auto-Responder

Architecture alignment: Modules A -> B -> C -> D -> E

| Scope note: The attached Razorpay brief defines the defense-only merchant-loss objective and evaluation bar, but it does not prescribe a Document Intelligence module. Module D in this document is the implementation architecture derived from the previously frozen ResolveAI Modules A-B-C design. It preserves the same case_id, security boundary, object-storage model, shared audit/error conventions and handoff to the later evidence-validation/ML pipeline. |
| --- |

## 1. Position of Module D in the ResolveAI Architecture

Figure 1. Module D consumes only trusted evidence from Module C and hands structured evidence to Module E.

### 1.1 Module D Objective

Module D converts secure evidence files into structured, normalized and traceable evidence data. Its responsibilities are document loading, preprocessing, OCR or native-text extraction, layout retention, document-type verification, field extraction, value normalization, quality/confidence scoring and source provenance.

### 1.2 Hard Boundary with Module E

Module D DOES extract fields from one evidence document.

Module D DOES preserve field-level provenance: page, bounding box/source text reference and confidence.

Module D DOES assess document readability and extraction quality.

Module D DOES NOT decide whether invoice, payment, order and shipment values agree across different sources.

Module D DOES NOT calculate the final evidence completeness/risk score used by the classifier.

Module D DOES NOT issue ACCEPT, REVIEW or CONTEST decisions.

Cross-document consistency, feature engineering and risk-model inputs belong to Module E.

### 1.3 End-to-End D Processing Sequence

1. Receive an internal processing request for a case/document that Module C has marked CLEAN and READY_FOR_OCR.

2. Verify case_id, merchant ownership, document_id and evidence lifecycle status.

3. Create an idempotent document-processing job and enqueue it for a worker.

4. Retrieve the private evidence object through an internal service identity; never expose a public object URL.

5. Keep the original evidence immutable and create temporary/derived processing artifacts separately.

6. For digital PDFs, attempt native text extraction first; for scanned PDFs/images, render pages and run image preprocessing.

7. Correct orientation/skew, normalize image quality where safe, and record every preprocessing operation.

8. Run OCR and preserve page/block coordinates, confidence and text provenance.

9. Validate/detect document type and compare it with Module C evidence_type.

10. Apply a versioned field schema for the document type and extract expected fields.

11. Normalize dates, amounts, IDs, currency and privacy-sensitive values without fabricating missing values.

12. Compute field confidence and document-quality metrics; route low-confidence cases to review.

13. Persist structured extraction records in PostgreSQL and large text/layout artifacts in private object storage.

14. Mark the document processing result COMPLETED / REVIEW_REQUIRED / FAILED.

15. Update the case-level Module D summary. When all blocking evidence is processed, make the case eligible for Module E.

## 2. Input Contract from Modules A-B-C

### 2.1 Required Preconditions

| ID | Precondition | Priority |
| --- | --- | --- |
| D-IN-01 | case_id exists in cases and belongs to the authenticated merchant/tenant. | Must |
| D-IN-02 | document_id exists in evidence_documents and references the same case_id. | Must |
| D-IN-03 | evidence_documents.scan_status = CLEAN. | Must |
| D-IN-04 | evidence_documents.processing_status = READY_FOR_OCR (or configured equivalent). | Must |
| D-IN-05 | object_key resolves to a private object; object hash still matches stored sha256 before processing where integrity recheck is enabled. | Must |
| D-IN-06 | evidence_type is one of the controlled categories introduced in Module C. | Must |
| D-IN-07 | case.processing_state is compatible with OCR/document processing; rejected/closed cases are not processed unless explicitly re-opened. | Must |

### 2.2 Fields Reused from Existing Module C Table

| Existing Column | Module D Use |
| --- | --- |
| document_id | Primary evidence identifier used by every Module D record. |
| case_id | Central ResolveAI case key; unchanged from Modules A-C. |
| merchant_id | Tenant boundary; used for defense-in-depth checks. |
| evidence_type | Expected evidence category and extraction-schema selector. |
| object_key | Private object-storage location of the immutable original. |
| mime_type | Validated type from Module C. |
| file_size_bytes | Processing/quota metadata. |
| sha256 | Integrity and duplicate reference. |
| scan_status | Must be CLEAN before D can read the file. |
| processing_status | Extended by Module D for OCR/extraction lifecycle. |

### 2.3 Module D Queue Message

{ "task": "PROCESS_EVIDENCE_DOCUMENT", "case_id": "CASE-00001", "document_id": "DOC-00007", "merchant_id": "MERCHANT-01", "idempotency_key": "DOC-00007:pipeline-v1", "requested_by": "system" }

## 3. Detailed Technical Requirements

| ID | Area | Requirement | Priority | Acceptance / Validation |
| --- | --- | --- | --- | --- |
| D-01 | Eligibility gate | Process only documents that passed Module C security controls and belong to the same case/merchant. | Must | Unsafe/quarantined/cross-tenant evidence is rejected before object retrieval. |
| D-02 | Immutable original | Never modify or overwrite the original object uploaded in Module C. | Must | Original SHA-256 remains unchanged after every D processing run. |
| D-03 | Async worker | Run OCR/extraction asynchronously through Redis/Celery or equivalent worker queue. | Must | API returns job reference without blocking for the full OCR duration. |
| D-04 | Idempotent jobs | Use document_id + pipeline/version-based idempotency; retries must not duplicate final rows. | Must | Repeated PROCESS request returns/reuses existing successful job where policy permits. |
| D-05 | Native PDF text first | For text-based PDFs, use native text/layout extraction before OCR when reliable. | Should | Digital PDF does not incur unnecessary OCR and preserves better text fidelity. |
| D-06 | Page rendering | Render scanned/mixed PDFs to page images with controlled resolution for OCR. | Must | Each page gets a deterministic page_number and derived artifact reference. |
| D-07 | Image preprocessing | Support orientation correction, deskew, safe denoise/contrast normalization and resize without inventing content. | Must | Preprocessing operations are stored in metadata and can be reproduced. |
| D-08 | OCR engine | Use PaddleOCR/docTR/Tesseract or equivalent engine behind an adapter interface. | Must | Engine can be replaced without changing Module E contract. |
| D-09 | Layout provenance | Preserve page/block coordinates and confidence for extracted text. | Must | Every important extracted field can point back to source page/region. |
| D-10 | Document-type validation | Detect/confirm document class and compare with evidence_type supplied by Module C. | Must | Mismatch creates TYPE_MISMATCH/REVIEW_REQUIRED rather than silently relabeling. |
| D-11 | Versioned extraction schema | Maintain expected field schemas per evidence_type and schema_version. | Must | Extraction rules can evolve without invalidating older runs. |
| D-12 | Field extraction | Extract only fields defined for the document type; missing fields remain NULL/UNKNOWN. | Must | No fabricated invoice/order/tracking/refund values. |
| D-13 | Value normalization | Normalize date, numeric amount, currency, IDs and controlled status values into canonical forms. | Must | Module E receives comparable typed values rather than free-form OCR strings. |
| D-14 | PII minimisation | Mask/hash privacy-sensitive values when raw values are not required downstream. | Must | No unnecessary raw personal data is copied into logs or feature tables. |
| D-15 | Confidence scoring | Store OCR confidence, field confidence and overall extraction confidence separately. | Must | Low-confidence field can be reviewed even if document-level score is high. |
| D-16 | Quality assessment | Capture readability indicators such as blur, skew, OCR coverage, resolution and cropping suspicion. | Should | Low-quality evidence is distinguishable from genuinely missing evidence. |
| D-17 | Review routing | Use configurable thresholds to mark REVIEW_REQUIRED; do not auto-correct uncertain fields. | Must | Low-confidence/mismatch cases remain available for human correction. |
| D-18 | Error taxonomy | Use structured errors for password-protected, corrupted, unsupported, timeout, OCR failure and extraction failure. | Must | Worker/client can distinguish retryable from permanent failures. |
| D-19 | Retries | Retry transient OCR/storage/worker errors using bounded exponential backoff. | Must | No infinite retry loops; terminal state is explicit. |
| D-20 | Versioning | Persist pipeline, OCR engine and extractor versions for every processing run. | Must | Any extraction can be reproduced/audited by version. |
| D-21 | Observability | Log case_id, document_id, job_id, stage, duration and error_code without sensitive OCR content. | Must | One document is traceable across queue, worker, DB and object storage. |
| D-22 | Private artifacts | Keep page images, OCR text and layout JSON private; use internal object keys/signed short-lived access only. | Must | No public evidence or OCR artifact URLs. |
| D-23 | No business decision | D must not produce ACCEPT/REVIEW/CONTEST based on financial risk; review here refers only to extraction quality. | Must | Risk state remains untouched by D. |
| D-24 | Module E handoff | Publish a stable structured output only after blocking document jobs finish according to policy. | Must | Module E receives typed fields + confidence + provenance + quality status. |

## 4. Document Processing Pipeline

### 4.1 Stage Pipeline

Module C: READY_FOR_OCR | v Eligibility + tenant + integrity checks | v Create idempotent processing job | v Private object retrieval | +--> Digital PDF? --> Native text/layout extraction | +--> Scan/Image --> Render page -> orientation/deskew -> OCR | v Page/block text + coordinates + confidence | v Document-type confirmation | v Versioned field extraction | v Canonical normalization + quality/confidence | +--> REVIEW_REQUIRED / FAILED | +--> COMPLETED | v Case Module-D summary -> eligible for Module E

### 4.2 Document-Type Extraction Requirements

| Evidence Type | Minimum Structured Fields | Boundary / Notes |
| --- | --- | --- |
| INVOICE | invoice_number, invoice_date, order_id/reference, total_amount, currency, merchant_reference, customer_reference(masked/hashed if needed) | Amount/date/order identifiers; no cross-document comparison in D. |
| PROOF_OF_DELIVERY | tracking_id, order_id/reference, delivery_date, delivery_status, recipient_reference, address_reference/hash, signature_present | Preserve source page and region for delivery facts. |
| COURIER_TRACKING | courier_name, tracking_id, order_reference, dispatch_date, delivery_date, shipment_status | Timeline fields are normalized but consistency is checked in E. |
| REFUND_RECEIPT | refund_id/reference, payment_id/order_reference, refund_amount, currency, refund_date, refund_status | Do not infer whether refund resolves dispute. |
| CUSTOMER_COMMUNICATION | message/document date, channel if observable, order/payment references, sender/recipient references (masked), normalized text artifact | D extracts/reconstructs text; semantic evidence interpretation belongs downstream. |
| SERVICE_CONFIRMATION | service_reference, order_reference, service_date, fulfillment/status fields explicitly present | No generated claims beyond the evidence. |
| TERMS_ACCEPTANCE | policy/terms reference, acceptance_date/time if present, customer/order reference, acceptance indicator explicitly visible | Preserve provenance for later policy validation. |
| OTHER | clean text/layout + generic identifiers where confidently recognized | Default to review if no approved extraction schema exists. |

### 4.3 Canonical Normalization Rules

| Value Type | Rule | Example |
| --- | --- | --- |
| Amounts | Preserve raw OCR text; parse numeric value separately; store ISO currency when explicitly known. | 12,499.00 INR -> numeric_value=12499.00, currency_code=INR |
| Dates | Normalize to ISO date/time only when unambiguous; preserve raw value and confidence. | 16/08/2026 -> 2026-08-16 if locale/schema supports it |
| Identifiers | Trim whitespace; normalize common separators conservatively; do not guess missing characters. | ORD 23988 -> ORD23988 only if configured rule is deterministic |
| Names/PII | Store only when needed; otherwise masked representation or hash for later equality checks. | Recipient value -> masked display + normalized hash |
| Statuses | Map only explicit document terms to controlled values; unknown remains UNKNOWN. | Delivered -> DELIVERED |
| Missing fields | Represent as NULL/UNKNOWN, never as 0/false unless the source explicitly states it. | No refund ID found -> NULL, not 'no refund' |

## 5. Confidence, Quality and Provenance Requirements

### 5.1 Confidence Layers

| Metric | Meaning | Use |
| --- | --- | --- |
| OCR block confidence | Engine confidence for recognized block/token. | Used for extraction diagnostics; not a risk score. |
| Field confidence | Confidence that a specific normalized field is supported by the source evidence. | Stored per field; Module E can use it as evidence-quality input. |
| Document extraction confidence | Aggregate confidence for the structured extraction. | Must not hide low-confidence critical fields. |
| Quality score | Readability/processing quality independent of semantic validity. | Distinguishes poor scan from absent evidence. |

### 5.2 Required Field Provenance

For every extracted business-critical field, Module D should preserve enough provenance for a reviewer to verify it:

document_id and extraction_id

page_number

source bounding box or text-block reference when available

masked/source snippet reference or source_text_hash

raw recognized value (masked if sensitive)

normalized canonical value

OCR confidence and field confidence

pipeline/extractor version

### 5.3 Suggested MVP Review Thresholds

Use thresholds as configuration, not hard-coded business truth. These are starting values for the hackathon and must be tuned on the team’s controlled evidence set.

| Condition | Module D Action |
| --- | --- |
| field_confidence >= 0.85 | AUTO-ACCEPT extraction field for downstream use, subject to Module E validation. |
| 0.60 <= field_confidence < 0.85 | Keep value but mark FIELD_REVIEW_RECOMMENDED. |
| field_confidence < 0.60 | Treat field as unreliable/UNKNOWN unless manually corrected. |
| document type mismatch | REVIEW_REQUIRED regardless of extraction confidence. |
| corrupted/password-protected/unsupported document | FAILED or REVIEW_REQUIRED according to recovery policy. |

## 6. Security, Privacy and Reliability

| Area | Requirement |
| --- | --- |
| Object access | Worker uses private service credentials and minimum required permission; never public bucket/object. |
| Temporary files | Use isolated temp directory; random names; delete after processing; do not persist secrets or raw evidence unnecessarily. |
| Malware boundary | Do not bypass Module C scan_status. If object/hash changed after scan, fail integrity validation. |
| Sandboxing | Run parsers/OCR in restricted worker/container where practical; treat PDFs/images as untrusted input. |
| Resource controls | Enforce page count, pixel/resolution, decompression and processing-time limits to resist resource-exhaustion files. |
| Logging | Do not log full OCR text, raw customer names, addresses or evidence contents. |
| Manual correction | If implemented, corrections require authenticated reviewer, audit event, original extracted value and corrected value/version. |
| Model/version audit | Every extraction stores model/pipeline version and config hash. |
| Retention | Follow the same evidence retention/deletion policy as Module C; derived artifacts cannot outlive originals without policy. |
| Failure isolation | One malformed document must not fail processing for unrelated cases. |

## 7. State Machine Integration with Existing Modules

### 7.1 Evidence Document processing_status

| Status | Meaning |
| --- | --- |
| READY_FOR_OCR | Produced by Module C; eligible for D. |
| OCR_QUEUED | D job created/enqueued. |
| OCR_PROCESSING | Worker owns active processing. |
| EXTRACTED | Structured extraction persisted successfully. |
| REVIEW_REQUIRED | Extraction exists but document/type/quality requires review. |
| OCR_FAILED | Terminal or temporarily failed after retry policy. |
| REPROCESS_REQUESTED | Explicit versioned re-run requested; optional. |

### 7.2 Case-Level State Rule

| To preserve the existing A-B-C state machine, Module D must NOT set cases.processing_state = FEATURE_READY. During D, the case can remain OCR_PROCESSING. Module D records completion in its own case_document_intelligence_status summary. Module E is responsible for cross-document validation/feature engineering and then advances the case to FEATURE_READY. |
| --- |

## 8. Module D API and Worker Contracts

| API | Purpose | Key Validation |
| --- | --- | --- |
| POST /api/v1/documents/{document_id}/process | Create/reuse a processing job. | Authenticated/authorized internal or analyst action; document must be CLEAN/READY_FOR_OCR. |
| GET /api/v1/documents/{document_id}/processing | Return latest job/status/quality summary. | Merchant/case ownership or privileged reviewer. |
| GET /api/v1/documents/{document_id}/extraction | Return structured extraction + field confidence/provenance. | Do not expose unrestricted raw object path. |
| POST /api/v1/documents/{document_id}/reprocess | Request a versioned re-run. | Reviewer/admin only; explicit pipeline version/policy. |
| GET /api/v1/cases/{case_id}/document-intelligence | Return case-level D readiness summary. | Case ownership and RBAC. |

## 9. Module D Database Design

Module D reuses the shared PostgreSQL database and object storage established in Modules A-C. Large page images, full OCR text and layout JSON should remain in private object storage; PostgreSQL stores metadata, typed extracted fields, quality/confidence, provenance references and processing state.

### 9.1 Existing Tables Reused

| Existing Object | Module D Responsibility |
| --- | --- |
| cases | Reuse case_id, merchant_id and processing_state. D does not create a second case record. |
| evidence_documents | Authoritative Module C evidence metadata. D reads security fields and extends processing_status values. |
| audit_logs / shared audit service | Record processing/reprocess/manual-correction/security-sensitive actions using the existing shared audit model. |
| merchants / app_users | Reuse tenant and actor identities; no duplicate user/merchant tables. |

### 9.2 New Module D Tables

| Table | Purpose |
| --- | --- |
| document_processing_jobs | One row per versioned OCR/extraction run; idempotency, status, timings and model versions. |
| document_pages | Page-level processing metadata, artifact references, quality and OCR confidence. |
| document_extractions | Document-level structured extraction result and overall status/confidence. |
| extracted_fields | Typed, normalized field values with field confidence and provenance. |
| document_quality_assessments | Explicit image/document readability metrics and quality flags. |
| document_model_versions | Registry for OCR/extractor/pipeline versions used by jobs. |
| case_document_intelligence_status | Case-level summary and readiness gate for Module E. |

#### Table D1 - document_processing_jobs

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| job_id | UUID | PK | Processing-run identifier. |
| document_id | UUID/VARCHAR | FK -> evidence_documents, INDEX | Evidence document. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Owning case. |
| merchant_id | UUID/VARCHAR | FK -> merchants, INDEX | Tenant boundary. |
| job_type | VARCHAR(40) | NOT NULL | OCR_AND_EXTRACT / REPROCESS. |
| status | VARCHAR(30) | NOT NULL, INDEX | QUEUED/PROCESSING/COMPLETED/REVIEW_REQUIRED/FAILED. |
| attempt_no | INT | NOT NULL DEFAULT 1 | Bounded retry count. |
| idempotency_key | VARCHAR(160) | UNIQUE, NOT NULL | Prevents duplicate processing for same version. |
| pipeline_version | VARCHAR(60) | NOT NULL | End-to-end D pipeline version. |
| ocr_model_version_id | UUID/VARCHAR | FK -> document_model_versions | OCR component used. |
| extractor_model_version_id | UUID/VARCHAR | FK -> document_model_versions | Extractor component used. |
| queued_at | TIMESTAMPTZ | NOT NULL | Queue time. |
| started_at | TIMESTAMPTZ | NULL | Worker start. |
| completed_at | TIMESTAMPTZ | NULL | Terminal time. |
| duration_ms | INT | NULL | Operational metric. |
| error_code | VARCHAR(80) | NULL, INDEX | Structured failure code. |
| error_message_masked | VARCHAR(500) | NULL | Safe diagnostic; no raw evidence/PII. |

#### Table D2 - document_pages

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| page_id | BIGSERIAL | PK | Page processing row. |
| job_id | UUID | FK -> document_processing_jobs, INDEX | Processing run. |
| document_id | UUID/VARCHAR | FK -> evidence_documents, INDEX | Evidence document. |
| page_number | INT | NOT NULL | 1-based page number. |
| native_text_used | BOOLEAN | NOT NULL DEFAULT FALSE | Native PDF extraction used. |
| page_artifact_key | VARCHAR(500) | NULL | Private derived page image key. |
| ocr_text_object_key | VARCHAR(500) | NULL | Private normalized text artifact. |
| layout_object_key | VARCHAR(500) | NULL | Private block/layout JSON artifact. |
| width_px | INT | NULL | Rendered width. |
| height_px | INT | NULL | Rendered height. |
| rotation_degrees | DECIMAL(6,2) | NULL | Applied/detected rotation. |
| preprocessing_json | JSONB | NOT NULL DEFAULT '{}' | Reproducible operations. |
| ocr_confidence | DECIMAL(5,4) | NULL CHECK 0..1 | Page OCR confidence. |
| created_at | TIMESTAMPTZ | NOT NULL | Creation timestamp. |

#### Table D3 - document_extractions

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| extraction_id | UUID | PK | Document-level extraction ID. |
| job_id | UUID | FK -> document_processing_jobs, UNIQUE per successful run | Run. |
| document_id | UUID/VARCHAR | FK -> evidence_documents, INDEX | Document. |
| case_id | UUID/VARCHAR | FK -> cases, INDEX | Case. |
| expected_evidence_type | VARCHAR(60) | NOT NULL | Type assigned by Module C. |
| detected_document_type | VARCHAR(60) | NULL, INDEX | D classification/confirmation. |
| type_match_status | VARCHAR(30) | NOT NULL | MATCH/MISMATCH/UNCERTAIN. |
| extraction_status | VARCHAR(30) | NOT NULL, INDEX | COMPLETED/REVIEW_REQUIRED/FAILED. |
| schema_version | VARCHAR(40) | NOT NULL | Field schema version. |
| extracted_json | JSONB | NOT NULL DEFAULT '{}' | Convenient structured document payload. |
| overall_confidence | DECIMAL(5,4) | NULL CHECK 0..1 | Aggregate extraction confidence. |
| requires_review | BOOLEAN | NOT NULL DEFAULT FALSE | Extraction-quality review flag. |
| created_at | TIMESTAMPTZ | NOT NULL | Extraction time. |

#### Table D4 - extracted_fields

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| field_id | BIGSERIAL | PK | Normalized field row. |
| extraction_id | UUID | FK -> document_extractions, INDEX | Parent extraction. |
| document_id | UUID/VARCHAR | FK -> evidence_documents, INDEX | Source document. |
| field_name | VARCHAR(100) | NOT NULL, INDEX | Canonical field name. |
| value_type | VARCHAR(30) | NOT NULL | TEXT/DECIMAL/DATE/TIMESTAMP/BOOLEAN/ID/HASH. |
| canonical_value_text | TEXT | NULL | Normalized string form where appropriate. |
| numeric_value | DECIMAL(18,4) | NULL | Typed amount/numeric value. |
| date_value | DATE | NULL | Typed date. |
| timestamp_value | TIMESTAMPTZ | NULL | Typed timestamp. |
| currency_code | CHAR(3) | NULL | ISO-style currency when explicit. |
| normalized_hash | CHAR(64) | NULL, INDEX | Privacy-safe equality reference where needed. |
| raw_value_masked | VARCHAR(500) | NULL | Masked recognized value for review. |
| field_confidence | DECIMAL(5,4) | NOT NULL CHECK 0..1 | Field-level confidence. |
| page_number | INT | NULL | Source page. |
| source_bbox | JSONB | NULL | Bounding box/page coordinate. |
| source_text_hash | CHAR(64) | NULL | Reference/integrity for source text block. |
| review_status | VARCHAR(30) | NOT NULL DEFAULT 'NOT_REQUIRED' | NOT_REQUIRED/RECOMMENDED/CORRECTED/REJECTED. |
| created_at | TIMESTAMPTZ | NOT NULL | Creation time. |

#### Table D5 - document_quality_assessments

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| quality_id | BIGSERIAL | PK | Quality row. |
| job_id | UUID | FK -> document_processing_jobs, UNIQUE | Processing run. |
| document_id | UUID/VARCHAR | FK -> evidence_documents, INDEX | Document. |
| blur_score | DECIMAL(8,4) | NULL | Configured blur metric. |
| skew_degrees | DECIMAL(6,2) | NULL | Detected skew. |
| resolution_dpi | INT | NULL | Approximate resolution when known. |
| readable_ratio | DECIMAL(5,4) | NULL CHECK 0..1 | Readable region ratio. |
| ocr_coverage_ratio | DECIMAL(5,4) | NULL CHECK 0..1 | Text/OCR coverage metric. |
| cropping_suspected | BOOLEAN | NOT NULL DEFAULT FALSE | Potential cutoff. |
| low_contrast | BOOLEAN | NOT NULL DEFAULT FALSE | Quality flag. |
| quality_score | DECIMAL(5,4) | NULL CHECK 0..1 | Aggregate readability score. |
| quality_grade | VARCHAR(20) | NULL | GOOD/FAIR/POOR/UNKNOWN. |
| quality_flags | JSONB | NOT NULL DEFAULT '[]' | Detailed quality flags. |
| assessed_at | TIMESTAMPTZ | NOT NULL | Assessment time. |

#### Table D6 - document_model_versions

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| model_version_id | UUID/VARCHAR | PK | Version registry row. |
| component | VARCHAR(40) | NOT NULL, INDEX | OCR/CLASSIFIER/EXTRACTOR/PREPROCESSOR. |
| model_name | VARCHAR(120) | NOT NULL | Engine/model identifier. |
| version | VARCHAR(80) | NOT NULL | Model/software version. |
| config_hash | CHAR(64) | NOT NULL | Configuration fingerprint. |
| artifact_reference | VARCHAR(500) | NULL | Internal model/config reference. |
| active | BOOLEAN | NOT NULL DEFAULT TRUE | Selectable status. |
| created_at | TIMESTAMPTZ | NOT NULL | Registration time. |

#### Table D7 - case_document_intelligence_status

| Column | Type | Constraints / Index | Purpose |
| --- | --- | --- | --- |
| case_id | UUID/VARCHAR | PK, FK -> cases | One D summary per case. |
| total_safe_documents | INT | NOT NULL DEFAULT 0 | Eligible Module C documents. |
| queued_documents | INT | NOT NULL DEFAULT 0 | Queued. |
| processed_documents | INT | NOT NULL DEFAULT 0 | Terminal processed. |
| successful_documents | INT | NOT NULL DEFAULT 0 | EXTRACTED. |
| review_required_documents | INT | NOT NULL DEFAULT 0 | Quality/type review. |
| failed_documents | INT | NOT NULL DEFAULT 0 | Failed. |
| blocking_failures | INT | NOT NULL DEFAULT 0 | Failures preventing E handoff. |
| overall_status | VARCHAR(30) | NOT NULL, INDEX | PENDING/PROCESSING/READY_FOR_E/REVIEW_REQUIRED/FAILED. |
| ready_for_module_e | BOOLEAN | NOT NULL DEFAULT FALSE, INDEX | Explicit handoff gate. |
| updated_at | TIMESTAMPTZ | NOT NULL | Latest summary update. |

## 10. Database Relationships and Integrity Rules

cases (1) | +----< evidence_documents (N) [created by Module C] | +----< document_processing_jobs (N versioned runs) | | | +----< document_pages (N) | | | +----- document_quality_assessments (0..1 per job) | | | +----- document_extractions (0..1 per completed run) | | | +----< extracted_fields (N) | +---- case_document_intelligence_status is summarized by case_id document_model_versions is referenced by document_processing_jobs. Shared audit_logs remains the authoritative cross-module audit trail.

| Rule | Integrity Requirement |
| --- | --- |
| IR-D01 | document_processing_jobs.document_id must reference an evidence_documents row with the same case_id and merchant_id. |
| IR-D02 | Only CLEAN evidence is eligible for OCR/extraction. |
| IR-D03 | idempotency_key must be unique for the intended document + pipeline version/run policy. |
| IR-D04 | document_pages must be unique by job_id + page_number. |
| IR-D05 | A successful job should have at most one document_extractions row for that run. |
| IR-D06 | extracted_fields must reference the same document_id as their parent extraction. |
| IR-D07 | Field confidence and aggregate confidence values must be constrained to [0,1]. |
| IR-D08 | Original evidence object_key and sha256 are never updated by Module D. |
| IR-D09 | ready_for_module_e can be TRUE only when case policy has no blocking D failures and required safe documents reached terminal states. |
| IR-D10 | Deletion/retention of D artifacts must cascade or be orchestrated consistently with Module C evidence retention policy. |

### 10.1 Recommended Indexes

| Index | Purpose |
| --- | --- |
| document_processing_jobs(document_id, status) | Latest/active job lookup. |
| document_processing_jobs(case_id, status) | Case processing dashboard. |
| document_processing_jobs(idempotency_key) UNIQUE | Duplicate prevention. |
| document_pages(job_id, page_number) UNIQUE | Ordered page retrieval. |
| document_extractions(document_id, created_at DESC) | Latest extraction. |
| extracted_fields(extraction_id, field_name) | Module E field loading. |
| extracted_fields(document_id, field_name) | Document-level review/search. |
| case_document_intelligence_status(ready_for_module_e, overall_status) | Module E worker queue/readiness scan. |
| document_model_versions(component, active) | Resolve active model version. |

## 11. Module D Output Contract to Module E

DocumentIntelligenceResult { case_id, document_id, evidence_type, detected_document_type, type_match_status, extraction_status, schema_version, overall_confidence, quality { quality_score, quality_grade, flags[] }, fields[] { field_name, value_type, canonical_value, normalized_hash?, field_confidence, page_number, source_bbox?, source_text_hash?, review_status }, artifacts { normalized_text_object_key?, layout_object_key? }, pipeline_version, ready_for_validation }

Module E must consume the canonical typed fields, not scrape raw OCR text again. Raw/normalized text remains available only when Module E or later grounded RAG requires it under access control.

## 12. Module D Error Model

| Error Code | Class | Meaning / Action |
| --- | --- | --- |
| DOC_NOT_ELIGIBLE | Permanent | Document did not pass Module C gate or wrong lifecycle state. |
| TENANT_MISMATCH | Permanent/Security | case/document merchant mismatch. |
| OBJECT_NOT_FOUND | Retryable then terminal | Private evidence object unavailable. |
| HASH_MISMATCH | Permanent/Security | Object integrity differs from stored Module C hash. |
| PDF_PASSWORD_PROTECTED | Review/Permanent | Cannot safely process without approved password workflow. |
| DOCUMENT_CORRUPTED | Permanent/Review | Parser cannot load document. |
| PAGE_LIMIT_EXCEEDED | Permanent | Configured page/resource limit exceeded. |
| OCR_TIMEOUT | Retryable | OCR engine timeout. |
| OCR_ENGINE_ERROR | Retryable/Terminal | Engine failure according to attempt policy. |
| UNSUPPORTED_DOCUMENT_LAYOUT | Review | Extraction schema cannot reliably process layout. |
| DOCUMENT_TYPE_MISMATCH | Review | Detected type conflicts with evidence_type. |
| LOW_EXTRACTION_CONFIDENCE | Review | Critical fields below configured confidence. |
| EXTRACTION_FAILED | Retryable/Terminal | Structured extraction failed. |
| STORAGE_WRITE_FAILED | Retryable | Derived artifact persistence failed. |

## 13. Acceptance Tests

| Scenario | Expected Result |
| --- | --- |
| Clean digital invoice PDF | Native text is used where reliable; structured invoice fields are persisted with provenance. |
| Scanned rotated delivery receipt | Orientation corrected; OCR succeeds; preprocessing metadata recorded. |
| Valid multi-page tracking PDF | All pages processed in deterministic order; fields can cite source page. |
| Low-resolution blurred image | Quality score/flags indicate poor evidence; uncertain fields are review-required, not fabricated. |
| PDF renamed from unsupported/unsafe content | Should already be blocked by C; D independently rejects if eligibility/integrity checks fail. |
| Password-protected PDF | Controlled PDF_PASSWORD_PROTECTED result; no worker crash. |
| Wrong document category | Detected type mismatch results in REVIEW_REQUIRED; Module C evidence_type not silently overwritten. |
| Same process request twice | One idempotent processing result for the same configured pipeline version. |
| Transient OCR timeout | Bounded retry; no duplicate extraction rows. |
| Cross-merchant document access | Rejected and audited. |
| Missing invoice amount | Field remains NULL/UNKNOWN; not 0 and not guessed. |
| Sensitive recipient/customer value | Stored/masked according to PII policy; not printed in logs. |
| Successful D case | case_document_intelligence_status.ready_for_module_e = TRUE only when handoff criteria are met. |
| Successful output | Module E can load fields/confidence/provenance entirely from D contract without re-running OCR. |

## 14. Recommended Student Implementation Order

1. Freeze Module D status enums, output contract and database migration.

2. Implement evidence eligibility loader using existing evidence_documents/cases tables.

3. Implement document_processing_jobs with idempotent Celery/worker execution.

4. Implement private object retrieval and immutable temporary working copy.

5. Implement PDF/image loader and native-PDF-text decision path.

6. Implement page rendering + preprocessing and persist page metadata.

7. Integrate OCR adapter and store page text/layout artifacts privately.

8. Implement document-type confirmation and mismatch handling.

9. Implement versioned extraction schemas beginning with INVOICE, PROOF_OF_DELIVERY and COURIER_TRACKING.

10. Implement typed normalization and extracted_fields persistence.

11. Implement quality/confidence/provenance.

12. Implement case_document_intelligence_status and Module E readiness gate.

13. Add retry/error taxonomy, audit/observability and security tests.

14. Run end-to-end tests using the same synthetic cases/evidence created for Modules A-C.

### 14.1 Minimum Hackathon Scope vs Strong Scope

| Priority | Scope |
| --- | --- |
| MVP Must | PDF/JPEG/PNG loader; native PDF text; preprocessing; OCR; INVOICE/POD/TRACKING extraction; typed values; confidence; provenance; D database; Module E handoff. |
| Strong Add | REFUND_RECEIPT + CUSTOMER_COMMUNICATION extraction; quality scoring; type classifier; manual correction workflow; better layout-aware extraction. |
| Defer if Time Limited | Complex general document understanding model, embeddings/vectorization, semantic RAG, cross-document contradiction logic, risk decision. These belong later. |

## 15. Definition of Done for Module D

Module D processes only CLEAN/eligible evidence from Module C.

Original evidence remains immutable and private.

OCR/native-text processing works for PDF/JPEG/PNG and multi-page PDF.

At least the core evidence types have versioned extraction schemas.

Extracted values are typed/normalized and missing values remain UNKNOWN.

Every critical field has confidence and source provenance.

Quality/type mismatch can trigger extraction-level REVIEW_REQUIRED.

All new tables, foreign keys, unique constraints and indexes are applied.

Repeated processing is idempotent and retries do not duplicate results.

Errors are structured and observable without leaking raw evidence/PII.

Case-level D readiness summary is correct.

Module E can consume the D output directly for cross-document validation and feature engineering.

Automated tests cover normal, low-quality, malformed, retry, duplicate and cross-tenant cases.

## 16. Handoff to Module E

After Module D is complete, Module E should implement evidence validation and feature engineering. It will compare Module D extracted fields against the trusted transaction/order/shipment/refund records from Module B, calculate consistency flags and evidence-quality/completeness features, and produce the final model-ready feature vector. This preserves the architecture: A secures the dispute, B enriches transaction context, C secures evidence files, D converts evidence into structured facts, and E validates those facts across sources.

## 17. Architecture Basis

Source-derived requirement: the attached Razorpay AI Risk Manager brief requires a working defense-only solution for merchant loss, with honest held-out precision/recall and false-positive cost. The brief does not define Modules A-D or their database schema. The Module D design above is therefore the project architecture derived to support the previously implemented ResolveAI Modules A-C and the later measurable verifier/risk pipeline.
