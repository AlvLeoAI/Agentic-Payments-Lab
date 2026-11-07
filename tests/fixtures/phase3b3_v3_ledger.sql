-- Synthetic copy generated with published Phase 3B.3 source 00abf23.
BEGIN TRANSACTION;
CREATE TABLE agent_bindings (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    data TEXT NOT NULL
);
CREATE TABLE buyer_profile (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    instance_id TEXT NOT NULL,
    data TEXT NOT NULL
);
INSERT INTO "buyer_profile" VALUES(1,'b830d479eed54350bcf3267243a1db9c','{"mode":"offline_x402","policy":{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","grant":{"budget":30000,"expires_at":1800000600,"grant_id":"offline-grant","issued_at":1799999999,"operator_id":"operator","per_purchase_limit":10000,"purpose":"research","service_ids":["test-report"]},"max_timeout_seconds":60,"method":"POST","mode":"offline_x402","network":"eip155:84532","payer":"0x1111111111111111111111111111111111111111","recipient":"0x2222222222222222222222222222222222222222","resource":"https://merchant.invalid/report","service_id":"test-report"}}');
CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id TEXT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);
INSERT INTO "events" VALUES(1,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(2,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_signing_claimed','{"authorization_id":"offline-auth-b40f25421906a1bde08545761a9d6c8abe998ad5e7838443627f542eca50eaae","terms_hash":"767456ce4b01e9758c8198172af888d2ff8831745dfff2b0ccfad95b0d06c67e"}',1800000000);
INSERT INTO "events" VALUES(3,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_dispatch_authorized','{}',1800000000);
INSERT INTO "events" VALUES(4,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_outcome_unverified','{}',1800000000);
INSERT INTO "events" VALUES(5,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_reconciliation_observed','{"identity":"216de9169bf5267b8c9308803911417f8b8c61a75316a4c33ed8ea885d610e39","observation_hash":"fec04b229d30c9df90771374cbda81f116c5f36a9171fb68d554872285b35f4f","outcome":"transfer","reason":"validated_offline_records","records":{"canonical":{"block":{"hash":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","number":90,"timestamp":1800000001},"finalized_head_hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"},"failure":"none","head":{"hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","number":100,"timestamp":1800000100},"head_check":{"hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","number":100,"timestamp":1800000100},"limits":{"from_block":0,"max_reads":8,"timeout_seconds":5,"to_block":999},"profile":{"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","source":"offline_fixture"},"reads_attempted":8,"receipt":{"block_hash":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","block_number":90,"logs":[{"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","block_hash":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","block_number":90,"event":"AuthorizationUsed","log_index":7,"nonce":"0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112","payer":"0x1111111111111111111111111111111111111111","transaction_hash":"0xabababababababababababababababababababababababababababababababab"},{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","block_hash":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","block_number":90,"event":"Transfer","log_index":8,"payer":"0x1111111111111111111111111111111111111111","recipient":"0x2222222222222222222222222222222222222222","transaction_hash":"0xabababababababababababababababababababababababababababababababab"}],"success":true,"transaction_hash":"0xabababababababababababababababababababababababababababababababab"},"search":{"complete":true,"events":[{"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","block_hash":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","block_number":90,"event":"AuthorizationUsed","log_index":7,"nonce":"0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112","payer":"0x1111111111111111111111111111111111111111","transaction_hash":"0xabababababababababababababababababababababababababababababababab"}],"finalized_head_hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","from_block":0,"to_block":100},"source":"offline_fixture","state":{"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","block_hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","block_number":100,"nonce":"0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112","payer":"0x1111111111111111111111111111111111111111","used":true},"terms_hash":"767456ce4b01e9758c8198172af888d2ff8831745dfff2b0ccfad95b0d06c67e","transaction":{"call":{"amount":10000,"method":"transferWithAuthorization","nonce":"0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112","payer":"0x1111111111111111111111111111111111111111","recipient":"0x2222222222222222222222222222222222222222","valid_after":0,"valid_before":1800000060},"hash":"0xabababababababababababababababababababababababababababababababab","to":"0x036CbD53842c5426634e7929541eC2318f3dCF7e"}}}',1800000100);
INSERT INTO "events" VALUES(6,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_fixture_applied','{"evidence_hash":"78c5725102bd5fb6a058bf5703be7e5cc69d5e765bcfd32ab3dfa7482d1874db","phase":"fixture_settled"}',1800000100);
INSERT INTO "events" VALUES(7,'offline-purchase-452685cfbbe531a32c1cc8e91e3fbf4aed9c34d93c5ae100235f71c5fccf0ec6','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(8,'offline-purchase-452685cfbbe531a32c1cc8e91e3fbf4aed9c34d93c5ae100235f71c5fccf0ec6','offline_signing_claimed','{"authorization_id":"offline-auth-a38a19e637c01cc813aafa85170b88d7fe1dadd58f74cf9d0ddd6cc053241e8b","terms_hash":"3064674a83c9ce8a957ad1e93c9314aea5e9017e8fdc2b84fc0e80c415d148c6"}',1800000000);
INSERT INTO "events" VALUES(9,'offline-purchase-988ff2b5ee365616641e79c396a04d44da4c024e49f734c98a0577e050cf263b','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(10,'offline-purchase-988ff2b5ee365616641e79c396a04d44da4c024e49f734c98a0577e050cf263b','offline_cancellation','{"reason":"canceled_before_claim"}',1800000000);
INSERT INTO "events" VALUES(11,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_reconciliation_observed','{"identity":"25b2d75b761dcc2819b0589cd165c75347930d918d840805baccde6c1e5ac1da","observation_hash":"d2a9794f21d60f9ca049464201ca34f207b9833d96bd4986b458a511f999a4a1","outcome":"incident","reason":"finality_contradiction","records":{"canonical":null,"failure":"none","head":{"hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","number":100,"timestamp":1800000100},"head_check":{"hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","number":100,"timestamp":1800000100},"limits":{"from_block":0,"max_reads":8,"timeout_seconds":5,"to_block":999},"profile":{"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","source":"offline_fixture"},"reads_attempted":4,"receipt":null,"search":null,"source":"offline_fixture","state":{"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","block_hash":"0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","block_number":100,"nonce":"0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112","payer":"0x1111111111111111111111111111111111111111","used":false},"terms_hash":"767456ce4b01e9758c8198172af888d2ff8831745dfff2b0ccfad95b0d06c67e","transaction":null}}',1800000100);
INSERT INTO "events" VALUES(12,'offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline_reconciliation_incident','{"observation_hash":"d2a9794f21d60f9ca049464201ca34f207b9833d96bd4986b458a511f999a4a1","prior_evidence_hash":"78c5725102bd5fb6a058bf5703be7e5cc69d5e765bcfd32ab3dfa7482d1874db","reason":"finality_contradiction"}',1800000100);
CREATE TABLE grants (
    grant_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    budget INTEGER NOT NULL CHECK(typeof(budget)='integer' AND budget>=0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved)='integer' AND reserved>=0),
    spent INTEGER NOT NULL DEFAULT 0 CHECK(typeof(spent)='integer' AND spent>=0),
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
    CHECK(reserved+spent<=budget)
);
INSERT INTO "grants" VALUES('offline-grant','{"budget":30000,"expires_at":1800000600,"grant_id":"offline-grant","issued_at":1799999999,"operator_id":"operator","per_purchase_limit":10000,"purpose":"research","service_ids":["test-report"]}',30000,10000,10000,0);
CREATE TABLE purchases (
    purchase_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    policy_version TEXT NOT NULL DEFAULT 'phase1-v1',
    grant_id TEXT NOT NULL,
    request TEXT NOT NULL,
    quote TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>=0),
    intent TEXT,
    authorization_id TEXT UNIQUE,
    payment_state TEXT NOT NULL CHECK(payment_state IN
        ('denied','reserved','executing','unknown','settled','failed','not_required')),
    delivery_state TEXT NOT NULL DEFAULT 'not_requested'
        CHECK(delivery_state IN ('not_requested','failed','delivered')),
    payment_error TEXT,
    delivery_error TEXT,
    receipt TEXT,
    resource TEXT
);
CREATE TABLE quotes (quote_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    purchase_id TEXT NOT NULL UNIQUE
);
INSERT INTO "tasks" VALUES('task','offline-grant','offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829');
INSERT INTO "tasks" VALUES('still-reserved','offline-grant','offline-purchase-452685cfbbe531a32c1cc8e91e3fbf4aed9c34d93c5ae100235f71c5fccf0ec6');
INSERT INTO "tasks" VALUES('canceled-before-claim','offline-grant','offline-purchase-988ff2b5ee365616641e79c396a04d44da4c024e49f734c98a0577e050cf263b');
CREATE TABLE x402_attempts (
    purchase_id TEXT PRIMARY KEY REFERENCES tasks(purchase_id),
    grant_id TEXT NOT NULL REFERENCES grants(grant_id),
    authorization_id TEXT NOT NULL UNIQUE,
    nonce TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    terms TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
    dispatch_authorized INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_authorized IN (0,1)),
    phase TEXT NOT NULL CHECK(phase IN (
        'signing_claimed','canceled_before_dispatch','dispatch_authorized',
        'reconciliation_required','fixture_settled','fixture_unused')),
    reason TEXT NOT NULL,
    evidence TEXT
);
INSERT INTO "x402_attempts" VALUES('offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829','offline-grant','offline-auth-b40f25421906a1bde08545761a9d6c8abe998ad5e7838443627f542eca50eaae','0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112','3586b6582c4e455d87ec15b51642af77','1b3054f3abcac8c0879febb29e769062f60e0267e5d8809be72683449441642a','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","authorization_id":"offline-auth-b40f25421906a1bde08545761a9d6c8abe998ad5e7838443627f542eca50eaae","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","nonce":"0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112","payer":"0x1111111111111111111111111111111111111111","policy_hash":"0604b9bd0d05c8c02521e4903549fcc0f85cb4b1a7b5237ed237abd9b619b667","primary_type":"TransferWithAuthorization","purchase_id":"offline-purchase-2c8db48c0dee8a0313a332364dbf8fc75f318234a513d509a93abbc378165829","recipient":"0x2222222222222222222222222222222222222222","request_hash":"340ad770637e816cf6a65ed7ec8eb0325875ee144270dde16e96b0c34600a56a","requirements_hash":"8a3bb293aacfc6b55ad37748514dd2dea9328bd6e4dcf557a7a5586856fc17d3","valid_after":0,"valid_before":1800000060}',10000,1,'fixture_settled','offline_fixture_only','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","block_hash":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","block_number":90,"block_timestamp":1800000001,"finality":"finalized","kind":"transfer","network":"eip155:84532","nonce":"0xf40b53717374fe8e5bb70797982486d8767db0acecb25d19f51412e9e75dc112","nonce_used":true,"payer":"0x1111111111111111111111111111111111111111","recipient":"0x2222222222222222222222222222222222222222","source":"offline_fixture","terms_hash":"767456ce4b01e9758c8198172af888d2ff8831745dfff2b0ccfad95b0d06c67e","transaction_hash":"0xabababababababababababababababababababababababababababababababab","transaction_success":true}');
INSERT INTO "x402_attempts" VALUES('offline-purchase-452685cfbbe531a32c1cc8e91e3fbf4aed9c34d93c5ae100235f71c5fccf0ec6','offline-grant','offline-auth-a38a19e637c01cc813aafa85170b88d7fe1dadd58f74cf9d0ddd6cc053241e8b','0xc2bd0fac309fdf25edf6783658a7f28e87ae3d4dba7a53916c330b074602c085','ac0f6d8ce619490d8cd3aef096bbbf0d','1b3054f3abcac8c0879febb29e769062f60e0267e5d8809be72683449441642a','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","authorization_id":"offline-auth-a38a19e637c01cc813aafa85170b88d7fe1dadd58f74cf9d0ddd6cc053241e8b","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","nonce":"0xc2bd0fac309fdf25edf6783658a7f28e87ae3d4dba7a53916c330b074602c085","payer":"0x1111111111111111111111111111111111111111","policy_hash":"0604b9bd0d05c8c02521e4903549fcc0f85cb4b1a7b5237ed237abd9b619b667","primary_type":"TransferWithAuthorization","purchase_id":"offline-purchase-452685cfbbe531a32c1cc8e91e3fbf4aed9c34d93c5ae100235f71c5fccf0ec6","recipient":"0x2222222222222222222222222222222222222222","request_hash":"340ad770637e816cf6a65ed7ec8eb0325875ee144270dde16e96b0c34600a56a","requirements_hash":"8a3bb293aacfc6b55ad37748514dd2dea9328bd6e4dcf557a7a5586856fc17d3","valid_after":0,"valid_before":1800000060}',10000,0,'signing_claimed','claim_committed',NULL);
CREATE TABLE x402_cancellations (
    task_id TEXT PRIMARY KEY REFERENCES x402_tasks(task_id),
    recorded_at INTEGER NOT NULL CHECK(typeof(recorded_at)='integer' AND recorded_at>=0)
);
INSERT INTO "x402_cancellations" VALUES('canceled-before-claim',1800000000);
CREATE TABLE x402_tasks (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    request TEXT NOT NULL
);
INSERT INTO "x402_tasks" VALUES('task','{"purpose":"research","service_id":"test-report","subject":"synthetic"}');
INSERT INTO "x402_tasks" VALUES('still-reserved','{"purpose":"research","service_id":"test-report","subject":"synthetic"}');
INSERT INTO "x402_tasks" VALUES('canceled-before-claim','{"purpose":"research","service_id":"test-report","subject":"synthetic"}');
CREATE TRIGGER agent_bindings_no_update BEFORE UPDATE ON agent_bindings
BEGIN SELECT RAISE(ABORT, 'agent bindings are immutable'); END;
CREATE TRIGGER agent_bindings_no_delete BEFORE DELETE ON agent_bindings
BEGIN SELECT RAISE(ABORT, 'agent bindings are immutable'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER buyer_profile_no_update BEFORE UPDATE ON buyer_profile
BEGIN SELECT RAISE(ABORT, 'buyer profile is immutable'); END;
CREATE TRIGGER buyer_profile_no_delete BEFORE DELETE ON buyer_profile
BEGIN SELECT RAISE(ABORT, 'buyer profile is immutable'); END;
CREATE INDEX events_purchase_id ON events(purchase_id);
CREATE TRIGGER x402_tasks_no_update BEFORE UPDATE ON x402_tasks
BEGIN SELECT RAISE(ABORT, 'offline task binding is immutable'); END;
CREATE TRIGGER x402_tasks_no_delete BEFORE DELETE ON x402_tasks
BEGIN SELECT RAISE(ABORT, 'offline task binding is immutable'); END;
CREATE TRIGGER x402_attempts_identity BEFORE UPDATE OF
    purchase_id,grant_id,authorization_id,nonce,owner,fingerprint,terms,amount ON x402_attempts
BEGIN SELECT RAISE(ABORT, 'offline authorization is immutable'); END;
CREATE TRIGGER x402_attempts_no_delete BEFORE DELETE ON x402_attempts
BEGIN SELECT RAISE(ABORT, 'offline authorization cannot be deleted'); END;
CREATE TRIGGER x402_dispatch_no_reset BEFORE UPDATE OF dispatch_authorized ON x402_attempts
WHEN OLD.dispatch_authorized=1 AND NEW.dispatch_authorized=0
BEGIN SELECT RAISE(ABORT, 'dispatch authorization cannot be reset'); END;
CREATE TRIGGER x402_cancellations_no_update BEFORE UPDATE ON x402_cancellations
BEGIN SELECT RAISE(ABORT, 'offline cancellation is immutable'); END;
CREATE TRIGGER x402_cancellations_no_delete BEFORE DELETE ON x402_cancellations
BEGIN SELECT RAISE(ABORT, 'offline cancellation cannot be deleted'); END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('events',12);
COMMIT;
PRAGMA user_version=3;
