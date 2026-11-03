-- tests/integration/grants.sql — 整合測試用的最小權限帳號（MySQL 容器第一次啟動時，在 schema（myapp/tests/fixtures/schema.sql）之後執行）
-- 照 docs/rebuild/db_schema.sql 最後的選用區塊，只取標 [移植版]、[兩者] 的 GRANT。
-- 整合測試都用這個帳號跑，也等於驗證了這組權限夠用。密碼只給拋棄式的測試資料庫用。
CREATE USER 'adam_analysis'@'%' IDENTIFIED BY 'adam-test-only' REQUIRE SSL;
GRANT SELECT (service_id, service_status) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT UPDATE (service_status) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT SELECT ON adam.tbl_video_storage TO 'adam_analysis'@'%';
GRANT SELECT ON adam.tbl_audio_storage TO 'adam_analysis'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_service_score TO 'adam_analysis'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_charts_storage TO 'adam_analysis'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_text_storage TO 'adam_analysis'@'%';
-- new-feature N4、N5：/reports（db_schema.sql 選用區塊標 [兩者]、[移植版] 的報表權限）
GRANT SELECT ON adam.tbl_report TO 'adam_analysis'@'%';
GRANT UPDATE (report_status, failure_reason, ai_model, ai_prompt_version) ON adam.tbl_report TO 'adam_analysis'@'%';
GRANT SELECT (service_id, account_id, service_start, service_end, service_status) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT SELECT ON adam.tbl_service_score TO 'adam_analysis'@'%';
GRANT SELECT (account_id, account_name, account_username, account_level, account_occupation_name, account_photo_key) ON adam.tbl_account TO 'adam_analysis'@'%';
GRANT SELECT (service_id, text_name) ON adam.tbl_text_storage TO 'adam_analysis'@'%';
-- PLAN1 W1：服務結果與待辦（migration 005）：清掉上一次的、寫回這一次的；寫回時 WHERE service_outcome_run
GRANT UPDATE (service_outcome, service_todos, service_outcome_model, service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT SELECT (service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';
-- SQS（migration 007，T49）：分析工作、outbox、worker 的心跳（API、送出器、worker 都用這個帳號）
GRANT SELECT, INSERT, UPDATE ON adam.tbl_analysis_job TO 'adam_analysis'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_outbox TO 'adam_analysis'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_worker TO 'adam_analysis'@'%';
