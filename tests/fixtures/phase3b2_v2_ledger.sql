-- Synthetic Phase 3B.2 state generated with accepted 3fc0cc1 source.
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
INSERT INTO "buyer_profile" VALUES(1,'efd064a9c4a94e1b93bd4088027796f6','{"mode":"offline_x402","policy":{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","grant":{"budget":30000,"expires_at":1800000600,"grant_id":"offline-grant","issued_at":1799999999,"operator_id":"operator","per_purchase_limit":10000,"purpose":"research","service_ids":["test-report"]},"max_timeout_seconds":60,"method":"POST","mode":"offline_x402","network":"eip155:84532","payer":"0x1111111111111111111111111111111111111111","recipient":"0x2222222222222222222222222222222222222222","resource":"https://merchant.invalid/report","service_id":"test-report"}}');
CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id TEXT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);
INSERT INTO "events" VALUES(1,'offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(2,'offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c','offline_signing_claimed','{"authorization_id":"offline-auth-6edc99343d1569045bb3a936bd19164ab8c84e28370d250d49e7c06c3093d9e8","terms_hash":"20c59ecec177a0a3400ca471638f5ab5db92416ccef578584f36f484623656cb"}',1800000000);
INSERT INTO "events" VALUES(3,'offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c','offline_dispatch_authorized','{}',1800000000);
INSERT INTO "events" VALUES(4,'offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c','offline_outcome_unverified','{}',1800000000);
INSERT INTO "events" VALUES(5,'offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c','offline_fixture_applied','{"evidence_hash":"f8c8eba7f00b4ddb3938bf373fee5645fb5bb202cf25dc172855edd3dffbbd00","phase":"fixture_settled"}',1800000100);
INSERT INTO "events" VALUES(6,'offline-purchase-fb24058a09d2bf972f16ab62c5ec2360f889ce58ba217551f9221bd754dcd13b','offline_task_created','{}',1800000000);
INSERT INTO "events" VALUES(7,'offline-purchase-fb24058a09d2bf972f16ab62c5ec2360f889ce58ba217551f9221bd754dcd13b','offline_signing_claimed','{"authorization_id":"offline-auth-93f5cfb8879458bcf982df79dd0959367fc2a764c51e6b69404faae748c9658f","terms_hash":"4608d7fd6428e8cb7f0ac62e8c352ba5751af2242456b8c4518244c0665a3978"}',1800000000);
INSERT INTO "events" VALUES(8,'offline-purchase-fb24058a09d2bf972f16ab62c5ec2360f889ce58ba217551f9221bd754dcd13b','offline_dispatch_authorized','{}',1800000000);
INSERT INTO "events" VALUES(9,'offline-purchase-fb24058a09d2bf972f16ab62c5ec2360f889ce58ba217551f9221bd754dcd13b','offline_outcome_unverified','{}',1800000000);
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
INSERT INTO "tasks" VALUES('task','offline-grant','offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c');
INSERT INTO "tasks" VALUES('still-reserved','offline-grant','offline-purchase-fb24058a09d2bf972f16ab62c5ec2360f889ce58ba217551f9221bd754dcd13b');
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
INSERT INTO "x402_attempts" VALUES('offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c','offline-grant','offline-auth-6edc99343d1569045bb3a936bd19164ab8c84e28370d250d49e7c06c3093d9e8','0x2f453ee922fa3a3a1402afc6c9dfd625e2365217318e862edf079eb6909e9645','fb050876469c46f0a44198b6e0166eb1','1b3054f3abcac8c0879febb29e769062f60e0267e5d8809be72683449441642a','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","authorization_id":"offline-auth-6edc99343d1569045bb3a936bd19164ab8c84e28370d250d49e7c06c3093d9e8","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","nonce":"0x2f453ee922fa3a3a1402afc6c9dfd625e2365217318e862edf079eb6909e9645","payer":"0x1111111111111111111111111111111111111111","policy_hash":"0604b9bd0d05c8c02521e4903549fcc0f85cb4b1a7b5237ed237abd9b619b667","primary_type":"TransferWithAuthorization","purchase_id":"offline-purchase-02b536ce83690a70eae525443d79beb955588329f6ac93c3e2fc28339171417c","recipient":"0x2222222222222222222222222222222222222222","request_hash":"340ad770637e816cf6a65ed7ec8eb0325875ee144270dde16e96b0c34600a56a","requirements_hash":"8a3bb293aacfc6b55ad37748514dd2dea9328bd6e4dcf557a7a5586856fc17d3","valid_after":0,"valid_before":1800000060}',10000,1,'fixture_settled','offline_fixture_only','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","block_hash":"0x3333333333333333333333333333333333333333333333333333333333333333","block_number":123,"block_timestamp":1800000001,"finality":"finalized","kind":"transfer","network":"eip155:84532","nonce":"0x2f453ee922fa3a3a1402afc6c9dfd625e2365217318e862edf079eb6909e9645","nonce_used":true,"payer":"0x1111111111111111111111111111111111111111","recipient":"0x2222222222222222222222222222222222222222","source":"offline_fixture","terms_hash":"20c59ecec177a0a3400ca471638f5ab5db92416ccef578584f36f484623656cb","transaction_hash":"0x4444444444444444444444444444444444444444444444444444444444444444","transaction_success":true}');
INSERT INTO "x402_attempts" VALUES('offline-purchase-fb24058a09d2bf972f16ab62c5ec2360f889ce58ba217551f9221bd754dcd13b','offline-grant','offline-auth-93f5cfb8879458bcf982df79dd0959367fc2a764c51e6b69404faae748c9658f','0xc76f4e76e3c3ce26d4fa4d389d5b2427a597c41a0521ddabc0ddc49c2e21e6de','a9a818b4888245e8ab132eaf52d79f00','1b3054f3abcac8c0879febb29e769062f60e0267e5d8809be72683449441642a','{"amount":10000,"asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","authorization_id":"offline-auth-93f5cfb8879458bcf982df79dd0959367fc2a764c51e6b69404faae748c9658f","chain_id":84532,"domain_name":"USDC","domain_version":"2","network":"eip155:84532","nonce":"0xc76f4e76e3c3ce26d4fa4d389d5b2427a597c41a0521ddabc0ddc49c2e21e6de","payer":"0x1111111111111111111111111111111111111111","policy_hash":"0604b9bd0d05c8c02521e4903549fcc0f85cb4b1a7b5237ed237abd9b619b667","primary_type":"TransferWithAuthorization","purchase_id":"offline-purchase-fb24058a09d2bf972f16ab62c5ec2360f889ce58ba217551f9221bd754dcd13b","recipient":"0x2222222222222222222222222222222222222222","request_hash":"340ad770637e816cf6a65ed7ec8eb0325875ee144270dde16e96b0c34600a56a","requirements_hash":"8a3bb293aacfc6b55ad37748514dd2dea9328bd6e4dcf557a7a5586856fc17d3","valid_after":0,"valid_before":1800000060}',10000,1,'reconciliation_required','outcome_unverified',NULL);
CREATE TABLE x402_cancellations (
    task_id TEXT PRIMARY KEY REFERENCES x402_tasks(task_id),
    recorded_at INTEGER NOT NULL CHECK(typeof(recorded_at)='integer' AND recorded_at>=0)
);
CREATE TABLE x402_tasks (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    request TEXT NOT NULL
);
INSERT INTO "x402_tasks" VALUES('task','{"purpose":"research","service_id":"test-report","subject":"synthetic"}');
INSERT INTO "x402_tasks" VALUES('still-reserved','{"purpose":"research","service_id":"test-report","subject":"synthetic"}');
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
INSERT INTO "sqlite_sequence" VALUES('events',9);
COMMIT;
PRAGMA user_version=2;
