-- =============================================================================
-- 測試專用（tools/verify.sh、tests/run.sh）：adam_app、adam_analysis 在 001～007 時的權限，和正式環境一樣。
-- 內容是 schema.sql 最後選用區塊的 GRANT（拿掉註解；分析服務的 [假服務]、[移植版] 兩種都給），
-- 也就是使用者手動套用的 db_schema.sql 選用區塊＋grants_001_004、grants_005_006、grants_007。
-- 用 adam_admin 在 schema.sql 之後執行。
-- 之後的 migration（008 起）自己帶 GRANT，由 runner 執行，不加進這裡；這個檔案只有在正式環境另外手動改了權限時才跟著改。
-- verify.sh 會比對套用新 migration 前後這兩個帳號的權限：少了哪一個（例如 DROP PROCEDURE 之後沒有重新 GRANT）就失敗。
-- =============================================================================
USE adam;

-- ---- adam_app（桌面端）---------------------------------------------------------
GRANT SELECT ON adam.tbl_service TO 'adam_app'@'%';
GRANT SELECT ON adam.tbl_service_score TO 'adam_app'@'%';
GRANT SELECT ON adam.tbl_video_storage TO 'adam_app'@'%';
GRANT SELECT ON adam.tbl_audio_storage TO 'adam_app'@'%';
GRANT SELECT ON adam.tbl_charts_storage TO 'adam_app'@'%';
GRANT SELECT (account_id, account_username, account_name, account_level, account_occupation_name, account_photo_key) ON adam.tbl_account TO 'adam_app'@'%';
GRANT INSERT, UPDATE ON adam.tbl_account TO 'adam_app'@'%';
GRANT UPDATE (service_status) ON adam.tbl_service TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_get_login TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_get_account_name TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_service TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_video_storage TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_audio_storage TO 'adam_app'@'%';
-- 001～003
GRANT SELECT, INSERT ON adam.tbl_report TO 'adam_app'@'%';
GRANT UPDATE (report_status, failure_reason, published_at, published_by) ON adam.tbl_report TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_list_reports TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_get_account_password TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_change_account_password TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_change_account_username TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_list_service_report_states TO 'adam_app'@'%';
-- 006
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_list TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_detail TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_for_service TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_follow_up_options TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_create TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_add_marker TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_remove_marker TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_respond TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_acknowledge TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_link_follow_up TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_complete TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_adjust TO 'adam_app'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_coaching_cancel TO 'adam_app'@'%';

-- ---- adam_analysis（EC2 上的分析服務）--------------------------------------------
GRANT SELECT ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT SELECT (service_id, service_status) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT UPDATE (service_status) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT SELECT ON adam.tbl_video_storage TO 'adam_analysis'@'%';
GRANT SELECT ON adam.tbl_audio_storage TO 'adam_analysis'@'%';
GRANT SELECT (account_id, account_name) ON adam.tbl_account TO 'adam_analysis'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_service_score TO 'adam_analysis'@'%';
GRANT SELECT, INSERT, DELETE ON adam.tbl_charts_storage TO 'adam_analysis'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_service_score TO 'adam_analysis'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_charts_storage TO 'adam_analysis'@'%';
GRANT EXECUTE ON PROCEDURE adam.sp_insert_text_storage TO 'adam_analysis'@'%';
-- 001、004
GRANT SELECT ON adam.tbl_report TO 'adam_analysis'@'%';
GRANT UPDATE (report_status, failure_reason, ai_model, ai_prompt_version) ON adam.tbl_report TO 'adam_analysis'@'%';
GRANT SELECT (service_id, account_id, service_start, service_end, service_status) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT SELECT ON adam.tbl_service_score TO 'adam_analysis'@'%';
GRANT SELECT (account_id, account_name, account_username, account_level, account_occupation_name, account_photo_key) ON adam.tbl_account TO 'adam_analysis'@'%';
GRANT SELECT (service_id, text_name) ON adam.tbl_text_storage TO 'adam_analysis'@'%';
-- 005
GRANT UPDATE (service_outcome, service_todos, service_outcome_model, service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';
GRANT SELECT (service_outcome_run) ON adam.tbl_service TO 'adam_analysis'@'%';
-- 007
GRANT SELECT, INSERT, UPDATE ON adam.tbl_analysis_job TO 'adam_analysis'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_outbox TO 'adam_analysis'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON adam.tbl_analysis_worker TO 'adam_analysis'@'%';
