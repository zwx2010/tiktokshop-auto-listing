# Business Closed Loop Specification

## ADDED Requirements

### Requirement: Honest external integration status

The system SHALL report real connectivity and execution outcomes for TikTok, AI, Feishu, and CDP integrations, and MUST return a non-success state when credentials, runtime, network, or the provider response is unavailable.

#### Scenario: Missing credentials

- WHEN an integration operation is requested without its required credentials
- THEN the operation returns `not_configured` with the missing configuration keys and does not create a success record

#### Scenario: Provider failure

- WHEN a configured provider times out, rejects the request, or returns an invalid response
- THEN the operation returns `failed` or `unavailable` with a safe diagnostic and does not advance the business lifecycle

### Requirement: Consistent dashboard and API metrics

The dashboard and statistics API SHALL use the same active-product definition and SHALL identify the runtime database as MySQL.

#### Scenario: Active product count

- WHEN the dashboard and `/api/stats` are read against the same database
- THEN both report the same active product count and the dashboard does not claim SQLite runtime usage

### Requirement: Listing copy recovery and gating

The system SHALL provide a traceable copy-generation or copy-recovery path for `copy_missing` listings and MUST prevent export or publish preparation until valid copy exists.

#### Scenario: Copy generation succeeds

- WHEN a real configured copy provider returns valid title and description that pass content rules
- THEN the listing records the source and validation result and becomes eligible for the next gate

#### Scenario: Copy generation unavailable

- WHEN no copy provider is configured or the provider fails
- THEN the listing remains `copy_missing`, records the reason, and cannot be exported as publishable data

### Requirement: Unified pre-publish gates

The export and publish-preparation flow SHALL require valid copy, passed image quality checks, completed approval, and a supported account integration state.

#### Scenario: Gate rejection

- WHEN any required gate is missing or failed
- THEN the record is excluded from publishable export and the response identifies the failed gate

#### Scenario: Gate pass

- WHEN all required gates pass
- THEN the record is included in publishable export with an auditable gate summary

### Requirement: Regression-safe MySQL operation

The implementation SHALL continue to read and write business data through MySQL and SHALL preserve existing migrated records and foreign-key integrity.

#### Scenario: Existing migrated data

- WHEN the service starts against the migrated MySQL database
- THEN existing accounts, products, SKUs, listings, and task data remain readable and no orphan records are introduced by the new flow

## MODIFIED Requirements

### Requirement: Observable lifecycle states

Lifecycle states SHALL distinguish `not_configured`, `unavailable`, `failed`, `copy_missing`, `ready`, and successful provider outcomes without conflating them.

#### Scenario: Status presentation

- WHEN an operator reads the dashboard or API response
- THEN the displayed status is the persisted real outcome and includes a reason for non-success states
