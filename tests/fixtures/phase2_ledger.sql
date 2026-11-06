-- Synthetic fixture generated using accepted Phase 2 source at
-- 9a2a49fd8eb4ac1fe55d9bac657f2e2f48301beb; no external calls.
BEGIN TRANSACTION;
CREATE TABLE agent_bindings (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    data TEXT NOT NULL
);
INSERT INTO "agent_bindings" VALUES('fixture-agent','{"context":{"grant_id":"demo-grant","purchase_id":"purchase-782f738d71674f32e784ebde2e6b78eb3c6023c685d8120abc3cc2b954703391","task_id":"fixture-agent"},"goal":{"purpose":"research","quality":"basic","subject":"original-fixture-intent"}}');
CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id TEXT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);
INSERT INTO "events" VALUES(1,NULL,'grant_created','{"grant_id":"demo-grant"}',1767225600);
INSERT INTO "events" VALUES(2,'purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e','task_created','{"task_id":"demo-paid"}',1767225600);
INSERT INTO "events" VALUES(3,'purchase-aeb28681d6b6e2c2190aa681b5629f54ffeb0d9a44ab6662cafc07d91049d83b','task_created','{"task_id":"demo-denied"}',1767225600);
INSERT INTO "events" VALUES(4,'purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e','reserved','{"amount":10000,"reason":null}',1767225600);
INSERT INTO "events" VALUES(5,'purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e','authorization_decision','{"approved":true,"fingerprint":"885e53de64b052d2cb1a7d7fdf9e8afe73a4c6ead9daf58034e2a82f10aef068","grant_id":"demo-grant","policy_version":"phase1-v1"}',1767225600);
INSERT INTO "events" VALUES(6,'purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e','dispatch_recorded','{}',1767225600);
INSERT INTO "events" VALUES(7,'purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e','settled','{"reason":null}',1767225600);
INSERT INTO "events" VALUES(8,'purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e','delivered','{"reason":null}',1767225600);
INSERT INTO "events" VALUES(9,'purchase-aeb28681d6b6e2c2190aa681b5629f54ffeb0d9a44ab6662cafc07d91049d83b','denied','{"amount":10000,"reason":"quote_merchant_id_mismatch"}',1767225600);
INSERT INTO "events" VALUES(10,'purchase-aeb28681d6b6e2c2190aa681b5629f54ffeb0d9a44ab6662cafc07d91049d83b','authorization_decision','{"approved":false,"fingerprint":"3036831a6a02f66dd767fae10abb54555c092f7f64e90341c0dfb248c6ec35eb","grant_id":"demo-grant","policy_version":"phase1-v1"}',1767225600);
INSERT INTO "events" VALUES(11,'purchase-b75a304e38de0e64db53e5c4c16d6a06998f4e8ce907344e3d9fb53ed0596f43','task_created','{"task_id":"fixture-reserved"}',1767225600);
INSERT INTO "events" VALUES(12,'purchase-b75a304e38de0e64db53e5c4c16d6a06998f4e8ce907344e3d9fb53ed0596f43','reserved','{"amount":10000,"reason":null}',1767225600);
INSERT INTO "events" VALUES(13,'purchase-b75a304e38de0e64db53e5c4c16d6a06998f4e8ce907344e3d9fb53ed0596f43','authorization_decision','{"approved":true,"fingerprint":"085ff1aaae1e51a33429f1b81f08b9d3074da6022c53c378cab5bc75ed6551ee","grant_id":"demo-grant","policy_version":"phase1-v1"}',1767225600);
INSERT INTO "events" VALUES(14,'purchase-782f738d71674f32e784ebde2e6b78eb3c6023c685d8120abc3cc2b954703391','task_created','{"task_id":"fixture-agent"}',1767225600);
CREATE TABLE grants (
    grant_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    budget INTEGER NOT NULL CHECK(typeof(budget)='integer' AND budget>=0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved)='integer' AND reserved>=0),
    spent INTEGER NOT NULL DEFAULT 0 CHECK(typeof(spent)='integer' AND spent>=0),
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
    CHECK(reserved+spent<=budget)
);
INSERT INTO "grants" VALUES('demo-grant','{"budget":40000,"expires_at":1767229200,"grant_id":"demo-grant","issued_at":1767225600,"operator_id":"local-operator","per_purchase_limit":30000,"purpose":"research","service_ids":["report-free","report-standard","report-premium"]}',40000,10000,10000,0);
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
INSERT INTO "purchases" VALUES('purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e','885e53de64b052d2cb1a7d7fdf9e8afe73a4c6ead9daf58034e2a82f10aef068','phase1-v1','demo-grant','{"purpose":"research","service_id":"report-standard","subject":"offline-demo"}','{"amount":10000,"asset":"SIM-USDC","decimals":6,"expires_at":1767225660,"issued_at":1767225600,"merchant_id":"mock-research","network":"simulation:local","origin":"http://mock-provider.invalid","quote_id":"quote-5befbcb2d2503cafa5bd274ce158378cf9e1e0f6c31f351cd683ce1221d6b67e","recipient":"sim-merchant-research","request_hash":"93162141fd737b3677966612d96fa187c632e9443e83e7fc25a52561b81785b3","route":"/resources","service_id":"report-standard"}',10000,'{"amount":10000,"asset":"SIM-USDC","authorization_id":"sim-auth-cbbe94869d0525bfb5f98823c9de830a6bc52f45631bc3113a921d51a80dbda0","expires_at":1767225660,"merchant_id":"mock-research","network":"simulation:local","policy_version":"phase1-v1","purchase_id":"purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e","quote_hash":"b590fc285b84bd8893aef3f76c855a4f3eee1f6fce1348c39ca8e3370e3ce3b7","recipient":"sim-merchant-research","request_hash":"93162141fd737b3677966612d96fa187c632e9443e83e7fc25a52561b81785b3"}','sim-auth-cbbe94869d0525bfb5f98823c9de830a6bc52f45631bc3113a921d51a80dbda0','settled','delivered',NULL,NULL,'{"intent":{"amount":10000,"asset":"SIM-USDC","authorization_id":"sim-auth-cbbe94869d0525bfb5f98823c9de830a6bc52f45631bc3113a921d51a80dbda0","expires_at":1767225660,"merchant_id":"mock-research","network":"simulation:local","policy_version":"phase1-v1","purchase_id":"purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e","quote_hash":"b590fc285b84bd8893aef3f76c855a4f3eee1f6fce1348c39ca8e3370e3ce3b7","recipient":"sim-merchant-research","request_hash":"93162141fd737b3677966612d96fa187c632e9443e83e7fc25a52561b81785b3"},"mode":"simulated","receipt_id":"sim-receipt-a3358d160147a89fde26b140a42ff684e30d28daba7bc29a82ad06c61af2cb20","settled_at":1767225600}','{"purchase_id":"purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e","receipt_id":"sim-receipt-a3358d160147a89fde26b140a42ff684e30d28daba7bc29a82ad06c61af2cb20","resource":{"quality":"standard","request_hash":"93162141fd737b3677966612d96fa187c632e9443e83e7fc25a52561b81785b3","service_id":"report-standard","subject":"offline-demo","summary":"Simulated standard report for offline-demo."},"resource_hash":"9df7e641bb68460d8bfe106ea2803e2bbd6ca858a06bc9675b7b880e1767b213"}');
INSERT INTO "purchases" VALUES('purchase-aeb28681d6b6e2c2190aa681b5629f54ffeb0d9a44ab6662cafc07d91049d83b','3036831a6a02f66dd767fae10abb54555c092f7f64e90341c0dfb248c6ec35eb','phase1-v1','demo-grant','{"purpose":"research","service_id":"report-standard","subject":"offline-demo"}','{"amount":10000,"asset":"SIM-USDC","decimals":6,"expires_at":1767225660,"issued_at":1767225600,"merchant_id":"unauthorized-merchant","network":"simulation:local","origin":"http://mock-provider.invalid","quote_id":"quote-5befbcb2d2503cafa5bd274ce158378cf9e1e0f6c31f351cd683ce1221d6b67e","recipient":"sim-merchant-research","request_hash":"93162141fd737b3677966612d96fa187c632e9443e83e7fc25a52561b81785b3","route":"/resources","service_id":"report-standard"}',10000,NULL,NULL,'denied','not_requested','quote_merchant_id_mismatch',NULL,NULL,NULL);
INSERT INTO "purchases" VALUES('purchase-b75a304e38de0e64db53e5c4c16d6a06998f4e8ce907344e3d9fb53ed0596f43','085ff1aaae1e51a33429f1b81f08b9d3074da6022c53c378cab5bc75ed6551ee','phase1-v1','demo-grant','{"purpose":"research","service_id":"report-standard","subject":"migration-fixture"}','{"amount":10000,"asset":"SIM-USDC","decimals":6,"expires_at":1767225660,"issued_at":1767225600,"merchant_id":"mock-research","network":"simulation:local","origin":"http://mock-provider.invalid","quote_id":"quote-127d4d47232542f7d50027632403439c85fa674e1fa809041f54433622253535","recipient":"sim-merchant-research","request_hash":"14544ec65da18492163ed3e9b85e3ef651875dc7f2f496d6f68d042ddaaa3ea1","route":"/resources","service_id":"report-standard"}',10000,'{"amount":10000,"asset":"SIM-USDC","authorization_id":"sim-auth-c1c0b1e2e4986a6a5974ed86fcf0ae4470b08890c5ffa692e452c12c5e03025a","expires_at":1767225660,"merchant_id":"mock-research","network":"simulation:local","policy_version":"phase1-v1","purchase_id":"purchase-b75a304e38de0e64db53e5c4c16d6a06998f4e8ce907344e3d9fb53ed0596f43","quote_hash":"b99ce79c0f97fc842ee79595f948885b36cfd496b26285743c4bc82d60602095","recipient":"sim-merchant-research","request_hash":"14544ec65da18492163ed3e9b85e3ef651875dc7f2f496d6f68d042ddaaa3ea1"}','sim-auth-c1c0b1e2e4986a6a5974ed86fcf0ae4470b08890c5ffa692e452c12c5e03025a','reserved','not_requested',NULL,NULL,NULL,NULL);
CREATE TABLE quotes (quote_id TEXT PRIMARY KEY, data TEXT NOT NULL);
INSERT INTO "quotes" VALUES('quote-5befbcb2d2503cafa5bd274ce158378cf9e1e0f6c31f351cd683ce1221d6b67e','{"amount":10000,"asset":"SIM-USDC","decimals":6,"expires_at":1767225660,"issued_at":1767225600,"merchant_id":"mock-research","network":"simulation:local","origin":"http://mock-provider.invalid","quote_id":"quote-5befbcb2d2503cafa5bd274ce158378cf9e1e0f6c31f351cd683ce1221d6b67e","recipient":"sim-merchant-research","request_hash":"93162141fd737b3677966612d96fa187c632e9443e83e7fc25a52561b81785b3","route":"/resources","service_id":"report-standard"}');
INSERT INTO "quotes" VALUES('quote-127d4d47232542f7d50027632403439c85fa674e1fa809041f54433622253535','{"amount":10000,"asset":"SIM-USDC","decimals":6,"expires_at":1767225660,"issued_at":1767225600,"merchant_id":"mock-research","network":"simulation:local","origin":"http://mock-provider.invalid","quote_id":"quote-127d4d47232542f7d50027632403439c85fa674e1fa809041f54433622253535","recipient":"sim-merchant-research","request_hash":"14544ec65da18492163ed3e9b85e3ef651875dc7f2f496d6f68d042ddaaa3ea1","route":"/resources","service_id":"report-standard"}');
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    purchase_id TEXT NOT NULL UNIQUE
);
INSERT INTO "tasks" VALUES('demo-paid','demo-grant','purchase-ba25bfe6f9e57151fcddefb101a77bca2d7037e5e082bad4daa9ecd85693dc2e');
INSERT INTO "tasks" VALUES('demo-denied','demo-grant','purchase-aeb28681d6b6e2c2190aa681b5629f54ffeb0d9a44ab6662cafc07d91049d83b');
INSERT INTO "tasks" VALUES('fixture-reserved','demo-grant','purchase-b75a304e38de0e64db53e5c4c16d6a06998f4e8ce907344e3d9fb53ed0596f43');
INSERT INTO "tasks" VALUES('fixture-agent','demo-grant','purchase-782f738d71674f32e784ebde2e6b78eb3c6023c685d8120abc3cc2b954703391');
CREATE TRIGGER agent_bindings_no_update BEFORE UPDATE ON agent_bindings
BEGIN SELECT RAISE(ABORT, 'agent bindings are immutable'); END;
CREATE TRIGGER agent_bindings_no_delete BEFORE DELETE ON agent_bindings
BEGIN SELECT RAISE(ABORT, 'agent bindings are immutable'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('events',14);
COMMIT;
