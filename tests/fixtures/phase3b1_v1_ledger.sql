-- Synthetic pre-correction Phase 3B.1 fixture, generated from the saved source.
-- Includes original bindings, claims, nonce/owner identities, cancellation and audit.
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
INSERT INTO "buyer_profile" VALUES(1,'9b80dda41b444bc4a9c4451a768f4756','{"mode":"offline_x402","policy":{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","grant":{"budget":30000,"expires_at":1800000600,"grant_id":"offline-grant","issued_at":1799999999,"operator_id":"operator","per_purchase_limit":10000,"purpose":"research","service_ids":["test-report"]},"max_timeout_seconds":60,"method":"POST","mode":"offline_x402","network":"eip155:84532","payer":"0x1111111111111111111111111111111111111111","recipient":"0x2222222222222222222222222222222222222222","resource":"https://merchant.invalid/report","service_id":"test-report"}}');
CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id TEXT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);
INSERT INTO "events" VALUES(1,'offline-purchase-f9e91deead31c216c614f76118a93b55fdcb9a2ba0eb38bc0a7c602e678eaa42','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(2,'offline-purchase-10e07c237ae72b2a0e92f43d83a355d9ae6f6689c553fd8e4b20ef65b6d01e05','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(3,'offline-purchase-8cc926e03ae1ddc08c65364a776dd6dabe89db768a8bc1aa18ebcf98e45f5183','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(4,'offline-purchase-91bbce24dd2f6fde0b0019fc7a1d72d69503d73d1c88b326ea488a954943687e','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(5,'offline-purchase-10e07c237ae72b2a0e92f43d83a355d9ae6f6689c553fd8e4b20ef65b6d01e05','offline_signing_claimed','{"authorization_id":"offline-auth-3bb4aee9d80c783a3fd76c2af8ac51d0093a872825ab72bce2449e7683990563","terms_hash":"3c60f4f24515f53b8077fc523b1371389428ba9783f3c97ea6f8455bccd1df38"}',1800000000);
INSERT INTO "events" VALUES(6,'offline-purchase-8cc926e03ae1ddc08c65364a776dd6dabe89db768a8bc1aa18ebcf98e45f5183','offline_signing_claimed','{"authorization_id":"offline-auth-324857ca2f2689acfdb39af84acfc11aeec14d26ac0c7cc3459371db906b7715","terms_hash":"a94cb89445b2ee11316fcc72654b4717dfeb84783bcf1c2f427d2c0733af89af"}',1800000000);
INSERT INTO "events" VALUES(7,'offline-purchase-8cc926e03ae1ddc08c65364a776dd6dabe89db768a8bc1aa18ebcf98e45f5183','offline_cancellation','{"reason":"canceled_before_dispatch"}',1800000000);
INSERT INTO "events" VALUES(8,'offline-purchase-91bbce24dd2f6fde0b0019fc7a1d72d69503d73d1c88b326ea488a954943687e','offline_signing_claimed','{"authorization_id":"offline-auth-bb3f13aa24ffd580ad70881591a38d2d327665bb6cb8dd7b68f190ad62d2da0a","terms_hash":"c47bf154fed3d912ce7c4be7744dc2527eb935b3c8a79c4f9878abc739253291"}',1800000000);
INSERT INTO "events" VALUES(9,'offline-purchase-91bbce24dd2f6fde0b0019fc7a1d72d69503d73d1c88b326ea488a954943687e','offline_dispatch_authorized','{}',1800000000);
INSERT INTO "events" VALUES(10,'offline-purchase-91bbce24dd2f6fde0b0019fc7a1d72d69503d73d1c88b326ea488a954943687e','offline_outcome_unverified','{}',1800000000);
CREATE TABLE grants (
    grant_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    budget INTEGER NOT NULL CHECK(typeof(budget)='integer' AND budget>=0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved)='integer' AND reserved>=0),
    spent INTEGER NOT NULL DEFAULT 0 CHECK(typeof(spent)='integer' AND spent>=0),
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
    CHECK(reserved+spent<=budget)
);
INSERT INTO "grants" VALUES('offline-grant','{"budget":30000,"expires_at":1800000600,"grant_id":"offline-grant","issued_at":1799999999,"operator_id":"operator","per_purchase_limit":10000,"purpose":"research","service_ids":["test-report"]}',30000,30000,0,0);
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
INSERT INTO "tasks" VALUES('not-started','offline-grant','offline-purchase-f9e91deead31c216c614f76118a93b55fdcb9a2ba0eb38bc0a7c602e678eaa42');
INSERT INTO "tasks" VALUES('claimed','offline-grant','offline-purchase-10e07c237ae72b2a0e92f43d83a355d9ae6f6689c553fd8e4b20ef65b6d01e05');
INSERT INTO "tasks" VALUES('canceled','offline-grant','offline-purchase-8cc926e03ae1ddc08c65364a776dd6dabe89db768a8bc1aa18ebcf98e45f5183');
INSERT INTO "tasks" VALUES('dispatched','offline-grant','offline-purchase-91bbce24dd2f6fde0b0019fc7a1d72d69503d73d1c88b326ea488a954943687e');
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
INSERT INTO "x402_attempts" VALUES('offline-purchase-10e07c237ae72b2a0e92f43d83a355d9ae6f6689c553fd8e4b20ef65b6d01e05','offline-grant','offline-auth-3bb4aee9d80c783a3fd76c2af8ac51d0093a872825ab72bce2449e7683990563','0x0928e67d117f18a639f5ae0ba8e49edbe585025959a5930e615f3578983ed8e1','4f3d7bf7229340b9bc5c674f337bd1ef','f096ee214945091d191119843d62b9d57b70b2751c07e3706d1313d0096ab1f8','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","authorization_id":"offline-auth-3bb4aee9d80c783a3fd76c2af8ac51d0093a872825ab72bce2449e7683990563","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","nonce":"0x0928e67d117f18a639f5ae0ba8e49edbe585025959a5930e615f3578983ed8e1","payer":"0x1111111111111111111111111111111111111111","policy_hash":"0604b9bd0d05c8c02521e4903549fcc0f85cb4b1a7b5237ed237abd9b619b667","primary_type":"TransferWithAuthorization","purchase_id":"offline-purchase-10e07c237ae72b2a0e92f43d83a355d9ae6f6689c553fd8e4b20ef65b6d01e05","recipient":"0x2222222222222222222222222222222222222222","request_hash":"249955ca375afc527b83b652f81cc9961bdc7416ae2e670a6c760878db240709","requirements_hash":"5de4a688c965abeb966a6da130448e25263d67593c2bb2331190c22e9dbee87f","valid_after":0,"valid_before":1800000060}',10000,0,'signing_claimed','claim_committed',NULL);
INSERT INTO "x402_attempts" VALUES('offline-purchase-8cc926e03ae1ddc08c65364a776dd6dabe89db768a8bc1aa18ebcf98e45f5183','offline-grant','offline-auth-324857ca2f2689acfdb39af84acfc11aeec14d26ac0c7cc3459371db906b7715','0x3894d4d5e673edd6940c15a8940294644f7a9052a8d7ca81a647b09f752407c2','1bb6c80dafab4cbdb6478c8493faee71','d9b0552ac38e33e8b8676158289eb29f49508ffe8320b4914e469eecb60bd0c4','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","authorization_id":"offline-auth-324857ca2f2689acfdb39af84acfc11aeec14d26ac0c7cc3459371db906b7715","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","nonce":"0x3894d4d5e673edd6940c15a8940294644f7a9052a8d7ca81a647b09f752407c2","payer":"0x1111111111111111111111111111111111111111","policy_hash":"0604b9bd0d05c8c02521e4903549fcc0f85cb4b1a7b5237ed237abd9b619b667","primary_type":"TransferWithAuthorization","purchase_id":"offline-purchase-8cc926e03ae1ddc08c65364a776dd6dabe89db768a8bc1aa18ebcf98e45f5183","recipient":"0x2222222222222222222222222222222222222222","request_hash":"91242ebfdc2da2075586cc6af244d8c97457051949eb5a4179748dff963da43f","requirements_hash":"5de4a688c965abeb966a6da130448e25263d67593c2bb2331190c22e9dbee87f","valid_after":0,"valid_before":1800000060}',10000,0,'canceled_before_dispatch','canceled_before_dispatch',NULL);
INSERT INTO "x402_attempts" VALUES('offline-purchase-91bbce24dd2f6fde0b0019fc7a1d72d69503d73d1c88b326ea488a954943687e','offline-grant','offline-auth-bb3f13aa24ffd580ad70881591a38d2d327665bb6cb8dd7b68f190ad62d2da0a','0x9a9dc91ea88fc33f522c753458008773d332028d3cb200eb5f9aad649d87ced5','bb272c0e1d084fe298246a8891ce66f6','6288015a322ce465309c366b6d48b156e63e15f3b871eee8b2b3ec4409c96ee7','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","authorization_id":"offline-auth-bb3f13aa24ffd580ad70881591a38d2d327665bb6cb8dd7b68f190ad62d2da0a","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","nonce":"0x9a9dc91ea88fc33f522c753458008773d332028d3cb200eb5f9aad649d87ced5","payer":"0x1111111111111111111111111111111111111111","policy_hash":"0604b9bd0d05c8c02521e4903549fcc0f85cb4b1a7b5237ed237abd9b619b667","primary_type":"TransferWithAuthorization","purchase_id":"offline-purchase-91bbce24dd2f6fde0b0019fc7a1d72d69503d73d1c88b326ea488a954943687e","recipient":"0x2222222222222222222222222222222222222222","request_hash":"b1c88a9bfdbc1c60358e98790dbbd3bfae31bd577c6f664d7a3f8206b810861d","requirements_hash":"5de4a688c965abeb966a6da130448e25263d67593c2bb2331190c22e9dbee87f","valid_after":0,"valid_before":1800000060}',10000,1,'reconciliation_required','outcome_unverified',NULL);
CREATE TABLE x402_tasks (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    request TEXT NOT NULL
);
INSERT INTO "x402_tasks" VALUES('not-started','{"purpose":"research","service_id":"test-report","subject":"not-started"}');
INSERT INTO "x402_tasks" VALUES('claimed','{"purpose":"research","service_id":"test-report","subject":"claimed"}');
INSERT INTO "x402_tasks" VALUES('canceled','{"purpose":"research","service_id":"test-report","subject":"canceled"}');
INSERT INTO "x402_tasks" VALUES('dispatched','{"purpose":"research","service_id":"test-report","subject":"dispatched"}');
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
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('events',10);
COMMIT;
PRAGMA user_version=1;
