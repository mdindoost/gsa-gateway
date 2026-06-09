-- Standard type vocabularies for a GSA Gateway v2 deployment.
-- These are reference lists the dashboard dropdowns read (org type, knowledge
-- type, contact role). They are DATA, not schema — the columns are unconstrained,
-- so any value still works; the vocab just guides admins toward consistency.
-- Stored on the root org (id 1) and inherited by every child org via settings
-- inheritance (getSettingJSON). Idempotent — safe to re-run.
--
-- Apply:  sqlite3 gsa_gateway.db < v2/scripts/seed_vocab.sql
-- Each deployment can edit these rows (or add per-org overrides) without code changes.

INSERT OR REPLACE INTO settings (org_id, key, value, type, description, updated_by) VALUES
(1, 'vocab.org_types',
 '["university","gsa","council","college","department","lab","club","office","event_series","faculty_group","custom"]',
 'json', 'Standard organization type vocabulary (custom allowed). Institutional nodes only — people are contact knowledge items.', 'dashboard');

INSERT OR REPLACE INTO settings (org_id, key, value, type, description, updated_by) VALUES
(1, 'vocab.knowledge_types',
 '["faq","policy","contact","resource","event_info","announcement","research","course","publication","custom"]',
 'json', 'Standard knowledge item type vocabulary (custom allowed).', 'dashboard');

INSERT OR REPLACE INTO settings (org_id, key, value, type, description, updated_by) VALUES
(1, 'vocab.contact_roles',
 '["president","vp_academic_affairs","vp_finance","secretary","officer","professor","associate_professor","assistant_professor","lecturer","lab_director","phd_student","ms_student","staff","advisor","admin","custom"]',
 'json', 'Standard roles for contact knowledge items. Used in contact metadata.', 'dashboard');
